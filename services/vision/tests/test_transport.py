"""WebSocket transport: handshake, framing, and error handling.

These exercise the adapter layer -- what happens to malformed input, an outdated
client, an oversized frame. A fake engine pool is injected so the transport can be
tested without a Stockfish binary; end-to-end behaviour with a real engine lives in
test_end_to_end.py.
"""

from __future__ import annotations

import json

import pytest
from chessview_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ClientInfo,
    FrameHeader,
    Hello,
    dump,
    encode_frame,
)
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from chessview_vision.app import create_app
from chessview_vision.detector import Detection, ScriptedDetector
from chessview_vision.testing import FakePool

JPEG = b"\xff\xd8not-a-real-image"


@pytest.fixture
def client():
    app = create_app(
        pool=FakePool(),
        detector_factory=lambda: ScriptedDetector([Detection("8/8/8/8/8/8/8/8", 0.9)], loop=True),
    )
    with TestClient(app) as test_client:
        yield test_client


def hello(version: int = PROTOCOL_VERSION, **kwargs) -> str:
    return dump(
        Hello(
            protocolVersion=version,
            client=ClientInfo(platform="test", appVersion="0.1.0"),
            **kwargs,
        )
    )


def test_health_reports_pool_state(client: TestClient):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["protocolVersion"] == PROTOCOL_VERSION
    assert body["pool"] == {"size": 1, "available": 1}


def test_handshake_returns_a_session_id(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ready = ws.receive_json()

    assert ready["type"] == "sessionReady"
    assert ready["protocolVersion"] == PROTOCOL_VERSION
    assert ready["sessionId"]
    assert ready["resumed"] is False


def test_an_outdated_client_is_told_so_and_disconnected(client: TestClient):
    """A version mismatch is rejected before any state exists, with a reason."""
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(version=PROTOCOL_VERSION + 1))
        error = ws.receive_json()

        assert error["type"] == "error"
        assert error["code"] == "protocol_version_mismatch"
        assert error["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_the_first_message_must_be_hello(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(json.dumps({"type": "pause", "paused": True}))
        error = ws.receive_json()

        assert error["code"] == "bad_message"
        assert error["fatal"] is True


def test_unparseable_handshake_is_rejected(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "bad_message"


def test_a_malformed_control_message_does_not_end_the_session(client: TestClient):
    """A client bug should cost one message, not the whole game."""
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()

        ws.send_text(json.dumps({"type": "pause", "paused": "yes please"}))
        error = ws.receive_json()
        assert error["code"] == "bad_message"
        assert error["fatal"] is False

        # Still alive.
        ws.send_text(json.dumps({"type": "ping", "ts": 1.0}))
        assert ws.receive_json()["type"] == "pong"


def test_an_oversized_frame_is_refused_without_buffering(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()

        ws.send_bytes(b"\x00" * (MAX_FRAME_BYTES + 1))
        error = ws.receive_json()
        assert error["code"] == "frame_too_large"
        assert error["fatal"] is False


def test_a_corrupt_binary_frame_is_reported_not_fatal(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()

        ws.send_bytes(b"\x00\x00\xff\xff" + b"garbage")
        error = ws.receive_json()
        assert error["code"] == "bad_message"
        assert error["fatal"] is False


def test_frames_are_accepted_after_calibration(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()

        ws.send_text(json.dumps({
            "type": "calibrate",
            "corners": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
            "orientation": "white_bottom",
        }))
        assert ws.receive_json()["type"] == "state"
        assert ws.receive_json()["type"] == "position"

        ws.send_bytes(encode_frame(FrameHeader(seq=1, captureTs=0.0, motion=True), JPEG))
        ws.send_text(json.dumps({"type": "ping", "ts": 2.0}))

        # The frame produces an unclear board (the scripted detector reports an
        # empty one); what matters is that the socket keeps serving.
        seen = [ws.receive_json() for _ in range(2)]
        assert seen[-1]["type"] == "pong"


def test_a_reconnecting_client_resumes_its_position(client: TestClient):
    """A dropped connection must not lose the game."""
    corrected = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"

    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        session_id = ws.receive_json()["sessionId"]
        ws.send_text(json.dumps({"type": "overrideFen", "fen": corrected}))
        ws.receive_json()  # state
        assert ws.receive_json()["fen"] == corrected

    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(resumeSessionId=session_id))
        ready = ws.receive_json()
        assert ready["resumed"] is True
        assert ready["sessionId"] == session_id
        assert ws.receive_json()["fen"] == corrected


def test_an_unknown_session_id_starts_a_fresh_session(client: TestClient):
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(resumeSessionId="does-not-exist"))
        ready = ws.receive_json()
        assert ready["resumed"] is False
        assert ready["sessionId"] != "does-not-exist"
