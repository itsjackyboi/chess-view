"""Protocol round-trip tests, including cross-language codec agreement.

The binary frame codec is implemented twice -- once in Python for the services and
once in TypeScript for the client. A pure-Python test would happily pass while the
two implementations disagreed on the wire, so the codec tests here drive the real
Node implementation through a subprocess.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chessview_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    Calibrate,
    ClientInfo,
    Corner,
    Eval,
    EvalLine,
    FrameDecodeError,
    FrameHeader,
    Hello,
    Orientation,
    Position,
    PositionSource,
    SessionStatus,
    State,
    decode_frame,
    dump,
    encode_frame,
    parse_client_message,
    parse_server_message,
)

PKG_ROOT = Path(__file__).resolve().parents[1]
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"payload-bytes" * 7


# npm workspaces hoist dependencies to the repo root, so look for either location.
REPO_ROOT = PKG_ROOT.parents[1]


def _node_available() -> bool:
    if shutil.which("node") is None:
        return False
    return any(
        (base / "node_modules" / "tsx").is_dir() for base in (PKG_ROOT, REPO_ROOT)
    )


requires_node = pytest.mark.skipif(
    not _node_available(), reason="node with tsx installed is required"
)


def _run_node(script: str) -> dict:
    """Run a snippet against the TypeScript codec, returning its JSON result."""
    result = subprocess.run(
        ["npx", "--no-install", "tsx", "-e", script],
        cwd=PKG_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# Message round-trips
# --------------------------------------------------------------------------


def test_hello_round_trips_through_discriminated_union():
    msg = Hello(
        protocolVersion=PROTOCOL_VERSION,
        client=ClientInfo(platform="ios", appVersion="0.1.0"),
    )
    parsed = parse_client_message(dump(msg))
    assert isinstance(parsed, Hello)
    assert parsed.client.platform == "ios"


def test_calibrate_round_trips_with_corners():
    msg = Calibrate(
        corners=[Corner(x=0, y=0), Corner(x=1, y=0), Corner(x=1, y=1), Corner(x=0, y=1)],
        orientation=Orientation.WHITE_BOTTOM,
    )
    parsed = parse_client_message(dump(msg))
    assert isinstance(parsed, Calibrate)
    assert len(parsed.corners) == 4
    assert parsed.corners[2].x == 1.0


def test_server_messages_round_trip():
    for msg in (
        Position(fen="8/8/8/8/8/8/8/8 w - - 0 1", confidence=0.9,
                 source=PositionSource.DETECTOR, ply=3, ts=1.0),
        Eval(fen="8/8/8/8/8/8/8/8 w - - 0 1", depth=12, final=False,
             lines=[EvalLine(multipv=1, scoreCp=-42, pv=["e2e4"])], ts=1.0),
        State(status=SessionStatus.UNCLEAR, detail="hand over board"),
    ):
        assert type(parse_server_message(dump(msg))) is type(msg)


def test_unknown_fields_are_rejected():
    """extra='forbid' means a typo'd field fails loudly instead of being ignored."""
    with pytest.raises(Exception):
        parse_client_message('{"type":"pause","paused":true,"pasued":false}')


def test_exclude_none_keeps_optional_fields_off_the_wire():
    payload = json.loads(dump(Position(
        fen="8/8/8/8/8/8/8/8 w - - 0 1", confidence=1.0,
        source=PositionSource.INITIAL, ply=0, ts=0.0,
    )))
    assert "moveUci" not in payload and "moveSan" not in payload


def test_eval_line_scores_are_mutually_exclusive_on_the_wire():
    line = json.loads(dump(Eval(
        fen="x", depth=1, final=True,
        lines=[EvalLine(multipv=1, scoreMate=3, pv=[])], ts=0.0,
    )))["lines"][0]
    assert line["scoreMate"] == 3
    assert "scoreCp" not in line


# --------------------------------------------------------------------------
# Binary frame codec
# --------------------------------------------------------------------------


def test_frame_round_trips_in_python():
    header = FrameHeader(seq=7, captureTs=1234.5, motion=True)
    decoded_header, decoded_jpeg = decode_frame(encode_frame(header, JPEG))
    assert decoded_header.seq == 7
    assert decoded_header.motion is True
    assert decoded_jpeg == JPEG


def test_oversized_frame_is_rejected_before_parsing():
    with pytest.raises(FrameDecodeError, match="limit is"):
        decode_frame(b"\x00" * (MAX_FRAME_BYTES + 1))


@pytest.mark.parametrize(
    "payload, match",
    [
        (b"\x00\x00", "too short"),
        (b"\x00\x00\xff\xff" + b"x", "exceeds frame size"),
        (b"\x00\x00\x00\x04" + b"nope" + b"x", "invalid frame header"),
    ],
)
def test_malformed_frames_raise(payload: bytes, match: str):
    with pytest.raises(FrameDecodeError, match=match):
        decode_frame(payload)


@requires_node
def test_typescript_decodes_a_python_encoded_frame():
    blob = encode_frame(FrameHeader(seq=42, captureTs=99.5, motion=True), JPEG)
    out = _run_node(
        "import {decodeFrame} from './src/index';"
        f"const bytes = Buffer.from('{blob.hex()}', 'hex');"
        "const {header, jpeg} = decodeFrame(new Uint8Array(bytes));"
        "console.log(JSON.stringify({header, jpegHex: Buffer.from(jpeg).toString('hex')}));"
    )
    assert out["header"] == {"seq": 42, "captureTs": 99.5, "motion": True}
    assert out["jpegHex"] == JPEG.hex()


@requires_node
def test_python_decodes_a_typescript_encoded_frame():
    out = _run_node(
        "import {encodeFrame} from './src/index';"
        f"const jpeg = new Uint8Array(Buffer.from('{JPEG.hex()}', 'hex'));"
        "const blob = encodeFrame({seq: 5, captureTs: 1.25, motion: false}, jpeg);"
        "console.log(JSON.stringify({hex: Buffer.from(blob).toString('hex')}));"
    )
    header, jpeg = decode_frame(bytes.fromhex(out["hex"]))
    assert (header.seq, header.capture_ts, header.motion) == (5, 1.25, False)
    assert jpeg == JPEG


@requires_node
def test_frame_size_limit_agrees_across_languages():
    """A limit that drifts would have the client sending frames the server drops."""
    out = _run_node(
        "import {MAX_FRAME_BYTES, PROTOCOL_VERSION} from './src/index';"
        "console.log(JSON.stringify({MAX_FRAME_BYTES, PROTOCOL_VERSION}));"
    )
    assert out["MAX_FRAME_BYTES"] == MAX_FRAME_BYTES
    assert out["PROTOCOL_VERSION"] == PROTOCOL_VERSION
