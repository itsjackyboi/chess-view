"""End-to-end: camera frames in, positions and evaluations out.

This is the M0 exit criterion from docs/roadmap.md -- the whole pipeline proven
against a real uvicorn server and a real Stockfish, with only the detector stubbed.
Building it before any ML lands is the point: when the vision model arrives at M2 it
drops into a pipeline that is already known to work.

A real server and a real async client are used rather than TestClient because
evaluations arrive from a background task. Anything that reads with a synchronous
fence sees the control-plane reply and misses them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import chess
import pytest
import pytest_asyncio
import uvicorn
import websockets
from chessview_protocol import (
    PROTOCOL_VERSION,
    ClientInfo,
    FrameHeader,
    Hello,
    dump,
    encode_frame,
)

from chessview_engine.config import EngineConfig
from chessview_vision.app import create_app
from chessview_vision.config import VisionConfig
from chessview_vision.detector import StubDetector

_binary = EngineConfig.from_env().binary
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (os.path.isfile(_binary) and os.access(_binary, os.X_OK)),
        reason=f"no Stockfish binary at {_binary!r} (set CHESSVIEW_STOCKFISH)",
    ),
]

GAME = ["e4", "e5", "Nf3", "Nc6", "Bb5"]
FRAMES_PER_POSITION = 2
STABILITY_FRAMES = 2
JPEG = b"\xff\xd8" + b"pretend-this-is-a-rectified-board" * 8

CALIBRATE = json.dumps({
    "type": "calibrate",
    "corners": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
    "orientation": "white_bottom",
})


@pytest_asyncio.fixture
async def endpoint():
    app = create_app(
        engine_config=EngineConfig(
            binary=_binary, pool_size=1, threads=1, hash_mb=32,
            multipv=3, depth=14, movetime_ms=600,
        ),
        vision_config=VisionConfig(stability_frames=STABILITY_FRAMES, min_confidence=0.6),
        detector_factory=lambda: StubDetector.from_moves(
            GAME, frames_per_position=FRAMES_PER_POSITION
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}/v1/session"
    finally:
        server.should_exit = True
        await task


def _frame(seq: int) -> bytes:
    return encode_frame(
        FrameHeader(seq=seq, captureTs=time.time() * 1000, motion=True), JPEG
    )


async def _handshake(ws) -> None:
    await ws.send(dump(Hello(
        protocolVersion=PROTOCOL_VERSION,
        client=ClientInfo(platform="test", appVersion="0.1.0"),
    )))
    assert json.loads(await ws.recv())["type"] == "sessionReady"
    await ws.send(CALIBRATE)


async def _collect(ws, *, seconds: float) -> list[dict]:
    """Read everything that arrives within a window.

    Time-bounded rather than count-bounded because evaluations arrive
    asynchronously: how many land depends on how deep the engine gets.
    """
    messages: list[dict] = []
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            break
        messages.append(json.loads(raw))
    return messages


async def _await_type(ws, wanted: str, *, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if message["type"] == wanted:
            return message
    raise AssertionError(f"no {wanted!r} message within {timeout}s")


async def _play_scripted_game(ws) -> list[dict]:
    await _handshake(ws)
    await _await_type(ws, "position")

    for seq in range(1, (len(GAME) + 1) * FRAMES_PER_POSITION + 4):
        await ws.send(_frame(seq))
        # Let the server process each frame; a real client sends at 5 fps and this
        # keeps the stability gate from seeing an unrealistic burst.
        await asyncio.sleep(0.01)

    return await _collect(ws, seconds=4.0)


async def test_a_scripted_game_produces_positions_and_evaluations(endpoint: str):
    async with websockets.connect(endpoint) as ws:
        messages = await _play_scripted_game(ws)

    played = [m["moveSan"] for m in messages if m["type"] == "position" and m.get("moveSan")]
    assert played == GAME, f"expected the scripted game, got {played}"

    evals = [m for m in messages if m["type"] == "eval"]
    assert evals, "the engine produced no evaluations"
    for ev in evals:
        best = ev["lines"][0]
        assert best["multipv"] == 1
        assert ("scoreCp" in best) or ("scoreMate" in best)
        assert best["san"], "evaluations should carry SAN for the overlay"


async def test_positions_are_legal_successors_of_one_another(endpoint: str):
    """Every committed position is reachable from the last by one legal move.

    The property the whole tracking design exists to guarantee, checked through the
    real transport rather than against the tracker directly.
    """
    async with websockets.connect(endpoint) as ws:
        messages = await _play_scripted_game(ws)

    board = chess.Board()
    for position in (m for m in messages if m["type"] == "position"):
        if not position.get("moveUci"):
            continue
        move = chess.Move.from_uci(position["moveUci"])
        assert move in board.legal_moves, f"{move} is not legal in {board.fen()}"
        board.push(move)
        assert board.fen() == position["fen"], "server FEN disagrees with the move it reported"


async def test_time_from_move_to_first_evaluation_is_within_budget(endpoint: str):
    """The M0 latency target: a confirmed move reaches the client with analysis fast.

    Covers detection, tracking, engine dispatch and transport over a real socket. It
    excludes camera capture and the mobile network, which is why this budget is
    tighter than the ~1s end-to-end figure in docs/architecture.md.
    """
    async with websockets.connect(endpoint) as ws:
        await _handshake(ws)
        await _await_type(ws, "position")
        # Drain the calibration position's evaluations so they cannot be mistaken
        # for the move's.
        await _collect(ws, seconds=1.2)

        for seq in range(1, FRAMES_PER_POSITION + 1):
            await ws.send(_frame(seq))
            await asyncio.sleep(0.01)

        started = time.perf_counter()
        for seq in range(FRAMES_PER_POSITION + 1, FRAMES_PER_POSITION + STABILITY_FRAMES + 1):
            await ws.send(_frame(seq))

        position = await _await_type(ws, "position", timeout=5.0)
        position_ms = (time.perf_counter() - started) * 1000
        await _await_type(ws, "eval", timeout=5.0)
        eval_ms = (time.perf_counter() - started) * 1000

    assert position["moveSan"] == GAME[0]
    assert position_ms < 500, f"move to position took {position_ms:.0f}ms"
    assert eval_ms < 1000, f"move to first evaluation took {eval_ms:.0f}ms"


async def test_evaluations_deepen_over_time(endpoint: str):
    """Progressive analysis: the client gets something immediately, then better."""
    async with websockets.connect(endpoint) as ws:
        await _handshake(ws)
        await _await_type(ws, "position")
        messages = await _collect(ws, seconds=2.0)

    evals = [m for m in messages if m["type"] == "eval"]
    assert len(evals) >= 2, f"expected progressive updates, got {len(evals)}"
    depths = [e["depth"] for e in evals]
    assert depths == sorted(depths), f"depths went backwards: {depths}"
    assert evals[-1]["final"] is True


async def test_a_dropped_connection_resumes_with_the_same_position(endpoint: str):
    """Losing the socket mid-game must not lose the game."""
    async with websockets.connect(endpoint) as ws:
        await ws.send(dump(Hello(
            protocolVersion=PROTOCOL_VERSION,
            client=ClientInfo(platform="test", appVersion="0.1.0"),
        )))
        session_id = json.loads(await ws.recv())["sessionId"]
        await ws.send(CALIBRATE)
        await _await_type(ws, "position")

        for seq in range(1, FRAMES_PER_POSITION + STABILITY_FRAMES + 1):
            await ws.send(_frame(seq))
            await asyncio.sleep(0.01)
        before = await _await_type(ws, "position", timeout=5.0)
        assert before["moveSan"] == GAME[0]

    async with websockets.connect(endpoint) as ws:
        await ws.send(dump(Hello(
            protocolVersion=PROTOCOL_VERSION,
            client=ClientInfo(platform="test", appVersion="0.1.0"),
            resumeSessionId=session_id,
        )))
        ready = json.loads(await ws.recv())
        assert ready["resumed"] is True
        restored = await _await_type(ws, "position")

    assert restored["fen"] == before["fen"], "resumed session lost the position"
