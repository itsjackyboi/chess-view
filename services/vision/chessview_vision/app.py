"""WebSocket transport for the vision service.

A thin adapter: it owns connection lifecycle, framing and validation, and hands
everything else to Session. Keeping it thin is what lets the pipeline be tested
without a socket.

One WebSocket per session carries both directions -- JSON text frames for control
and binary frames for camera images -- so there is no second connection to manage
and no reconnect cost per frame.
"""

from __future__ import annotations

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
from chessview_vision.config import VisionConfig
from chessview_vision.detector import Detector, StubDetector
from chessview_vision.registry import SessionRegistry
from chessview_vision.session import Session

log = logging.getLogger(__name__)

# Close codes. 1008 is the WebSocket "policy violation" code, which is the closest
# standard fit for "your client is too old to talk to this server".
CLOSE_POLICY_VIOLATION = 1008
CLOSE_INTERNAL_ERROR = 1011

DetectorFactory = Callable[[], Detector]


def _default_detector() -> Detector:
    """The M0 stub: replays a scripted game, ignoring the image.

    Replaced by the real model at M2. Until then it is what makes the whole pipeline
    -- camera, transport, tracking, engine, overlay -- provable end to end.
    """
    return StubDetector.from_moves(
        ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"],
        frames_per_position=3,
        loop=True,
    )


def create_app(
    *,
    engine_config: EngineConfig | None = None,
    vision_config: VisionConfig | None = None,
    detector_factory: DetectorFactory = _default_detector,
    pool: EnginePool | None = None,
) -> FastAPI:
    """Build the ASGI app.

    `pool` and `detector_factory` are injection points: transport tests substitute
    both so they can exercise framing and handshake behaviour without a Stockfish
    binary or a vision model.
    """
    vision_config = vision_config or VisionConfig.from_env()
    pool = pool or EnginePool(engine_config or EngineConfig.from_env())
    registry = SessionRegistry(vision_config.session_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Engines are spawned before the first client connects, so no session pays
        # process startup or NNUE load. This is why the service wants persistent
        # hosts rather than scale-to-zero.
        await pool.start()
        try:
            yield
        finally:
            await pool.stop()

    app = FastAPI(title="ChessView vision service", lifespan=lifespan)
    app.state.pool = pool
    app.state.registry = registry

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok" if pool.available > 0 else "at_capacity",
            "engine": pool.engine_name,
            "pool": {"size": pool.size, "available": pool.available},
            "suspendedSessions": len(registry),
            "protocolVersion": PROTOCOL_VERSION,
        }

    @app.websocket("/v1/session")
    async def session_socket(websocket: WebSocket) -> None:
        await websocket.accept()

        async def send(message: BaseModel) -> None:
            await websocket.send_text(dump(message))

        hello = await _await_hello(websocket, send)
        if hello is None:
            return

        resumed = registry.resume(hello.resume_session_id) if hello.resume_session_id else None
        session_id = hello.resume_session_id if resumed else uuid.uuid4().hex

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
                await _pump(websocket, session, send)
        except EngineUnavailable as exc:
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

    return app


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


async def _pump(websocket: WebSocket, session: Session, send) -> None:
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
