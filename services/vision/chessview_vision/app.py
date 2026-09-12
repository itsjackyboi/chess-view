"""WebSocket transport for the vision service.

A thin adapter: it owns connection lifecycle, framing and validation, and hands
everything else to Session. Keeping it thin is what lets the pipeline be tested
without a socket.

One WebSocket per session carries both directions -- JSON text frames for control
and binary frames for camera images -- so there is no second connection to manage
and no reconnect cost per frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from chessview_engine.config import EngineConfig
from chessview_engine.pool import EnginePool, EngineUnavailable
from chessview_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    Error,
    ErrorCode,
    FrameDecodeError,
    Hello,
    decode_frame,
    dump,
    parse_client_message,
)
from chessview_vision.classifier import SquareClassifier
from chessview_vision.cnn_detector import CnnDetector
from chessview_vision.config import VisionConfig
from chessview_vision.detector import Detector, StubDetector
from chessview_vision.limits import ClientLimiter, FrameLimiter, LimitConfig
from chessview_vision.metrics import METRICS
from chessview_vision.registry import SessionRegistry
from chessview_vision.session import Session

log = logging.getLogger(__name__)

# Close codes. 1008 is the WebSocket "policy violation" code, which is the closest
# standard fit for "your client is too old to talk to this server".
CLOSE_POLICY_VIOLATION = 1008
CLOSE_INTERNAL_ERROR = 1011
# 1001 "going away" is what a client should see on a planned shutdown, as distinct
# from an error -- it means "this was deliberate, come back".
CLOSE_GOING_AWAY = 1001

# How long a shutdown waits for live sessions before stopping the engines anyway.
# Long enough for a client to receive the notice and close cleanly, short enough
# that a deploy is not held up by one stuck connection.
DRAIN_TIMEOUT_SECONDS = 10.0


class Draining:
    """Tracks shutdown state and how many sessions are still live."""

    def __init__(self) -> None:
        self._draining = False
        self._sessions = 0
        self._empty = asyncio.Event()
        self._empty.set()

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def live_sessions(self) -> int:
        return self._sessions

    def begin(self) -> None:
        self._draining = True

    def enter(self) -> None:
        self._sessions += 1
        self._empty.clear()

    def leave(self) -> None:
        self._sessions = max(0, self._sessions - 1)
        if self._sessions == 0:
            self._empty.set()

    async def wait_for_sessions(self, *, timeout: float) -> bool:
        """Wait for live sessions to finish. False if the timeout won."""
        if self._sessions == 0:
            return True
        log.info("draining: waiting for %d live session(s)", self._sessions)
        try:
            await asyncio.wait_for(self._empty.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("drain timed out with %d session(s) still open", self._sessions)
            return False

DetectorFactory = Callable[[], Detector]


def _stub_detector() -> Detector:
    """Replays a scripted game, ignoring the image.

    Used when no model is configured. It keeps the pipeline demonstrable and the
    transport tests deterministic, but it reports positions that have nothing to do
    with what the camera sees -- so the service says so at startup rather than
    letting it pass for working detection.
    """
    return StubDetector.from_moves(
        ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"],
        frames_per_position=3,
        loop=True,
    )


def _build_detector_factory(config: VisionConfig) -> DetectorFactory:
    """Choose the real detector when a model is available, else the stub.

    The classifier is loaded once and shared: an onnxruntime session is thread-safe
    for inference and costs real memory, so giving every session its own would waste
    both. The per-session state that does differ -- board geometry -- lives on the
    CnnDetector wrapper, which is cheap.
    """
    if config.model is None:
        if config.model_path:
            log.error(
                "CHESSVIEW_MODEL is set to %r but no file is there; "
                "falling back to the STUB detector, which reports scripted "
                "positions unrelated to the camera",
                config.model_path,
            )
        else:
            log.warning(
                "no CHESSVIEW_MODEL configured; using the STUB detector. "
                "Reported positions are scripted, not detected."
            )
        return _stub_detector

    classifier = SquareClassifier(config.model)
    log.info("using the trained square classifier at %s", config.model)

    def factory() -> Detector:
        return CnnDetector(classifier, expect_rectified=config.expect_rectified)

    return factory


def create_app(
    *,
    engine_config: EngineConfig | None = None,
    vision_config: VisionConfig | None = None,
    detector_factory: DetectorFactory | None = None,
    pool: EnginePool | None = None,
    limits: LimitConfig | None = None,
) -> FastAPI:
    """Build the ASGI app.

    `pool` and `detector_factory` are injection points: transport tests substitute
    both so they can exercise framing and handshake behaviour without a Stockfish
    binary or a vision model.
    """
    vision_config = vision_config or VisionConfig.from_env()
    detector_factory = detector_factory or _build_detector_factory(vision_config)
    pool = pool or EnginePool(engine_config or EngineConfig.from_env())
    registry = SessionRegistry(vision_config.session_ttl_seconds)
    limit_config = limits or LimitConfig()
    clients = ClientLimiter(limit_config)
    # Flipped on shutdown so in-flight sessions can be told what is happening
    # instead of having the socket vanish under them.
    draining = Draining()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Engines are spawned before the first client connects, so no session pays
        # process startup or NNUE load. This is why the service wants persistent
        # hosts rather than scale-to-zero.
        await pool.start()
        try:
            yield
        finally:
            # Refuse new sessions first, then give the live ones a moment to be told
            # the service is going away. Without this a deploy tears engines out from
            # under active sessions, and the client sees an unexplained drop that
            # looks identical to a network failure -- so it reconnects, to a server
            # that is still going down.
            draining.begin()
            await draining.wait_for_sessions(timeout=DRAIN_TIMEOUT_SECONDS)
            await pool.stop()

    app = FastAPI(title="ChessView vision service", lifespan=lifespan)
    app.state.pool = pool
    app.state.registry = registry

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": _health_status(draining, pool),
            "engine": pool.engine_name,
            "pool": {"size": pool.size, "available": pool.available},
            "suspendedSessions": len(registry),
            "protocolVersion": PROTOCOL_VERSION,
            # Surfaced so a deploy running on the stub is obvious from monitoring
            # rather than discovered by a confused user.
            "detector": "model" if vision_config.model else "stub",
        }

    @app.get("/metrics")
    async def metrics() -> dict:
        """Counters that say whether the service is doing its job.

        Request counts reveal very little here; detection confidence and the resync
        rate are what show the model meeting conditions it was not trained for.
        """
        snapshot = METRICS.snapshot()
        snapshot["limits"] = {
            "activeClients": clients.active_clients,
            "activeSessions": clients.active_sessions,
            "sessionRejections": clients.rejections,
        }
        snapshot["pool"] = {"size": pool.size, "available": pool.available}
        return snapshot

    @app.websocket("/v1/session")
    async def session_socket(websocket: WebSocket) -> None:
        await websocket.accept()

        async def send(message: BaseModel) -> None:
            await websocket.send_text(dump(message))

        if draining.is_draining:
            # Fatal, so the client stops retrying this instance and its load
            # balancer sends the next attempt somewhere that can serve it.
            await send(
                Error(
                    code=ErrorCode.ENGINE_UNAVAILABLE,
                    message="this server is shutting down; reconnect shortly",
                    fatal=True,
                )
            )
            await websocket.close(CLOSE_GOING_AWAY)
            return

        hello = await _await_hello(websocket, send)
        if hello is None:
            return

        resumed = registry.resume(hello.resume_session_id) if hello.resume_session_id else None
        session_id = hello.resume_session_id if resumed else uuid.uuid4().hex

        # Sessions are anonymous, so the source address is the only client identity
        # available. It is imperfect -- shared NAT groups strangers together -- which
        # is why the cap is a handful of sessions rather than one.
        client_key = websocket.client.host if websocket.client else "unknown"
        if not clients.try_acquire(client_key, session_id):
            METRICS.sessions_refused_rate_limit += 1
            await send(
                Error(
                    code=ErrorCode.RATE_LIMITED,
                    message="too many sessions open from this device",
                    fatal=True,
                )
            )
            await websocket.close(CLOSE_POLICY_VIOLATION)
            return

        METRICS.sessions_started += 1
        if resumed is not None:
            METRICS.sessions_resumed += 1

        draining.enter()
        try:
            async with pool.lease() as engine:
                session = Session(
                    session_id,
                    detector=detector_factory(),
                    engine=engine,
                    send=send,
                    config=vision_config,
                )
                if resumed is not None:
                    session._tracker.set_position(resumed.fen)  # noqa: SLF001
                await session.greet(resumed=resumed is not None)
                await _pump(websocket, session, send, FrameLimiter(limit_config))
        except EngineUnavailable as exc:
            METRICS.sessions_refused_capacity += 1
            log.warning("refusing session %s: %s", session_id, exc)
            with contextlib.suppress(RuntimeError):
                await send(
                    Error(
                        code=ErrorCode.ENGINE_UNAVAILABLE,
                        message="analysis is at capacity, try again shortly",
                        fatal=True,
                    )
                )
                await websocket.close(CLOSE_INTERNAL_ERROR)
        finally:
            clients.release(client_key, session_id)
            draining.leave()

    return app


def _health_status(draining: "Draining", pool: EnginePool) -> str:
    """What a load balancer should do with this instance.

    `draining` is distinct from `at_capacity`: capacity is temporary and the
    instance still wants traffic afterwards, whereas a draining instance is going
    away and should be taken out of rotation.
    """
    if draining.is_draining:
        return "draining"
    return "ok" if pool.available > 0 else "at_capacity"


async def _await_hello(websocket: WebSocket, send) -> Hello | None:
    """Read and validate the opening handshake.

    A version mismatch is rejected before any state is created -- an old client
    talking a protocol we no longer speak should be told so, not left to fail on a
    message it cannot parse.
    """
    try:
        raw = await websocket.receive_text()
        message = parse_client_message(raw)
    except (WebSocketDisconnect, RuntimeError):
        return None
    except (ValidationError, ValueError) as exc:
        await send(Error(code=ErrorCode.BAD_MESSAGE, message=str(exc), fatal=True))
        await websocket.close(CLOSE_POLICY_VIOLATION)
        return None

    if not isinstance(message, Hello):
        await send(
            Error(
                code=ErrorCode.BAD_MESSAGE,
                message="first message must be 'hello'",
                fatal=True,
            )
        )
        await websocket.close(CLOSE_POLICY_VIOLATION)
        return None

    if message.protocol_version != PROTOCOL_VERSION:
        await send(
            Error(
                code=ErrorCode.PROTOCOL_VERSION_MISMATCH,
                message=(
                    f"server speaks protocol {PROTOCOL_VERSION}, "
                    f"client sent {message.protocol_version}"
                ),
                fatal=True,
            )
        )
        await websocket.close(CLOSE_POLICY_VIOLATION)
        return None

    return message


async def _pump(
    websocket: WebSocket, session: Session, send, frames: FrameLimiter
) -> None:
    """Dispatch inbound messages until the client goes away."""
    registry: SessionRegistry = websocket.app.state.registry
    try:
        while True:
            packet = await websocket.receive()

            if packet["type"] == "websocket.disconnect":
                break

            if (text := packet.get("text")) is not None:
                try:
                    await session.handle_message(parse_client_message(text))
                except (ValidationError, ValueError) as exc:
                    # A malformed control message is the client's bug, not a reason
                    # to drop a session that is otherwise working.
                    await send(
                        Error(code=ErrorCode.BAD_MESSAGE, message=str(exc), fatal=False)
                    )
                continue

            if (payload := packet.get("bytes")) is not None:
                if len(payload) > MAX_FRAME_BYTES:
                    await send(
                        Error(
                            code=ErrorCode.FRAME_TOO_LARGE,
                            message=f"frame exceeds {MAX_FRAME_BYTES} bytes",
                            fatal=False,
                        )
                    )
                    continue
                # The client gates its own frame rate, but a client is not
                # something the server may rely on. Over-rate frames are dropped
                # silently rather than ending the session: the usual cause is a
                # misbehaving motion gate, and killing the connection would turn a
                # minor client bug into a broken app.
                if not frames.allow():
                    METRICS.frames_rate_limited += 1
                    continue

                try:
                    header, jpeg = decode_frame(payload)
                except FrameDecodeError as exc:
                    await send(
                        Error(code=ErrorCode.BAD_MESSAGE, message=str(exc), fatal=False)
                    )
                    continue
                await session.handle_frame(header, jpeg)
    except WebSocketDisconnect:
        pass
    finally:
        # Retain the position so a reconnect resumes the game, but release the
        # engine -- the pool slot is the scarce resource.
        registry.suspend(session.session_id, session.fen, session._tracker.ply)  # noqa: SLF001
        await session.close()


app = create_app()
