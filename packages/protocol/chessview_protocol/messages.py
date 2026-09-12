"""Wire protocol shared by the ChessView client, vision service and engine service.

These models are the single source of truth for the protocol. ``generate.py``
emits ``schema.json`` from them, and the TypeScript types consumed by the mobile
client are generated from that schema -- so the two ends cannot drift apart
without the generation check failing.

Transport is a single WebSocket per session carrying two kinds of frame:

* **Text frames** are JSON envelopes, discriminated on ``type``.
* **Binary frames** are camera frames, encoded by :func:`encode_frame` as
  ``[4-byte big-endian header length][JSON FrameHeader][JPEG bytes]``. Packing the
  header into the same frame as its payload keeps a frame and its metadata
  atomic; sending them as two messages would let them interleave under load.
"""

from __future__ import annotations

import json
import struct
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

# Frames larger than this are rejected outright rather than buffered. A rectified
# 320x320 JPEG is ~22 KB; 256 KB leaves generous headroom while capping the damage
# a malformed or hostile client can do to server memory.
MAX_FRAME_BYTES = 256 * 1024


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class SessionStatus(str, Enum):
    """What the pipeline is currently able to tell the user.

    The client renders each of these distinctly. ``UNCLEAR`` exists so we can say
    "I don't know" instead of showing a confident evaluation for a position we are
    not sure about -- showing stale or guessed analysis is worse than showing none.
    """

    CALIBRATING = "calibrating"
    TRACKING = "tracking"
    UNCLEAR = "unclear"
    NO_BOARD = "no_board"
    LOW_LIGHT = "low_light"
    PAUSED = "paused"


class PositionSource(str, Enum):
    INITIAL = "initial"
    DETECTOR = "detector"
    MANUAL = "manual"


class Orientation(str, Enum):
    WHITE_BOTTOM = "white_bottom"
    BLACK_BOTTOM = "black_bottom"


class ErrorCode(str, Enum):
    PROTOCOL_VERSION_MISMATCH = "protocol_version_mismatch"
    BAD_MESSAGE = "bad_message"
    FRAME_TOO_LARGE = "frame_too_large"
    NOT_CALIBRATED = "not_calibrated"
    ILLEGAL_POSITION = "illegal_position"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    SESSION_NOT_FOUND = "session_not_found"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal"


# --------------------------------------------------------------------------
# Client -> server
# --------------------------------------------------------------------------


class ClientInfo(_Base):
    platform: Literal["ios", "android", "test"]
    app_version: str = Field(alias="appVersion")


class Hello(_Base):
    type: Literal["hello"] = "hello"
    protocol_version: int = Field(alias="protocolVersion")
    client: ClientInfo
    # Present when resuming after a dropped connection. The server restores the
    # authoritative position rather than trusting the client's mirror of it.
    resume_session_id: str | None = Field(default=None, alias="resumeSessionId")


class Corner(_Base):
    x: float
    y: float


class Calibrate(_Base):
    """Lock the board geometry and seed the starting position.

    ``corners`` are the four board corners in the client's *rectified* frame of
    reference, clockwise from the corner nearest the user. The client owns the
    homography (it runs at camera frame rate on-device); the server records the
    corners only for diagnostics and for redrawing the guide on resume.
    """

    type: Literal["calibrate"] = "calibrate"
    corners: list[Corner] = Field(min_length=4, max_length=4)
    orientation: Orientation
    start_fen: str | None = Field(default=None, alias="startFen")


class OverrideFen(_Base):
    """A manual correction from the 2D board editor.

    Respected as authoritative until the detector observes a further change, so a
    user who fixes a misread piece does not have it immediately overwritten.
    """

    type: Literal["overrideFen"] = "overrideFen"
    fen: str


class Pause(_Base):
    type: Literal["pause"] = "pause"
    paused: bool


class Ping(_Base):
    type: Literal["ping"] = "ping"
    ts: float


ClientMessage = Annotated[
    Union[Hello, Calibrate, OverrideFen, Pause, Ping],
    Field(discriminator="type"),
]


class FrameHeader(_Base):
    """Metadata packed into the binary frame alongside the JPEG payload."""

    seq: int
    # Client monotonic clock, milliseconds. Used only as an opaque round-trip
    # token -- we never compare it against server time, since the clocks are
    # unrelated.
    capture_ts: float = Field(alias="captureTs")
    # Set when the client's on-device motion gate fired for this frame. The server
    # uses it to decide whether a frame is a keepalive or part of a move burst.
    motion: bool = False


# --------------------------------------------------------------------------
# Server -> client
# --------------------------------------------------------------------------


class EngineInfo(_Base):
    name: str
    version: str
    multipv: int


class SessionReady(_Base):
    type: Literal["sessionReady"] = "sessionReady"
    session_id: str = Field(alias="sessionId")
    protocol_version: int = Field(alias="protocolVersion")
    engine: EngineInfo
    resumed: bool = False


class Position(_Base):
    """A committed position. Only ever sent when the position actually changed.

    The server debounces and applies a stability check before committing, so the
    client can treat every ``Position`` as a real move rather than a candidate.
    """

    type: Literal["position"] = "position"
    fen: str
    # 0..1. The client refuses to present analysis confidently below a threshold.
    confidence: float = Field(ge=0.0, le=1.0)
    source: PositionSource
    ply: int
    move_uci: str | None = Field(default=None, alias="moveUci")
    move_san: str | None = Field(default=None, alias="moveSan")
    # Board indices (0 = a8, 63 = h1) the detector was least sure about. The
    # correction editor outlines these, so a user fixing a misread is pointed at the
    # squares most likely to be wrong instead of hunting for it.
    low_confidence_squares: list[int] = Field(
        default_factory=list, alias="lowConfidenceSquares"
    )
    ts: float


class EvalLine(_Base):
    """One MultiPV line.

    Exactly one of ``score_cp``/``score_mate`` is set. Both are normalised to
    **white's point of view** before leaving the server: Stockfish reports from the
    side to move, which would make the evaluation bar flip on every move.
    """

    multipv: int
    score_cp: int | None = Field(default=None, alias="scoreCp")
    score_mate: int | None = Field(default=None, alias="scoreMate")
    pv: list[str]
    san: list[str] = Field(default_factory=list)


class Eval(_Base):
    """A progressive evaluation update.

    Several of these arrive per position, at increasing depth. ``final`` marks the
    last one. Carrying ``fen`` lets the client discard updates that belong to a
    position it has already moved past.
    """

    type: Literal["eval"] = "eval"
    fen: str
    depth: int
    final: bool
    lines: list[EvalLine]
    ts: float


class State(_Base):
    type: Literal["state"] = "state"
    status: SessionStatus
    detail: str | None = None


class Error(_Base):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str
    fatal: bool = False


class Pong(_Base):
    type: Literal["pong"] = "pong"
    ts: float
    server_ts: float = Field(alias="serverTs")


ServerMessage = Annotated[
    Union[SessionReady, Position, Eval, State, Error, Pong],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Binary frame codec
# --------------------------------------------------------------------------


class FrameDecodeError(ValueError):
    """Raised when a binary frame is malformed or oversized."""


_HEADER_PREFIX = struct.Struct(">I")


def encode_frame(header: FrameHeader, jpeg: bytes) -> bytes:
    blob = header.model_dump_json(by_alias=True).encode("utf-8")
    return _HEADER_PREFIX.pack(len(blob)) + blob + jpeg


def decode_frame(payload: bytes) -> tuple[FrameHeader, bytes]:
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameDecodeError(
            f"frame is {len(payload)} bytes, limit is {MAX_FRAME_BYTES}"
        )
    if len(payload) < _HEADER_PREFIX.size:
        raise FrameDecodeError("frame is too short to contain a header length")
    (header_len,) = _HEADER_PREFIX.unpack_from(payload)
    start = _HEADER_PREFIX.size
    end = start + header_len
    if end > len(payload):
        raise FrameDecodeError("header length exceeds frame size")
    try:
        header = FrameHeader.model_validate_json(payload[start:end])
    except Exception as exc:  # pydantic raises a variety of shapes here
        raise FrameDecodeError(f"invalid frame header: {exc}") from exc
    return header, payload[end:]


# --------------------------------------------------------------------------
# JSON envelope helpers
# --------------------------------------------------------------------------


class _ClientEnvelope(BaseModel):
    message: ClientMessage


class _ServerEnvelope(BaseModel):
    message: ServerMessage


def parse_client_message(raw: str | bytes):
    """Parse and validate a client text frame, dispatching on ``type``."""
    return _ClientEnvelope.model_validate({"message": json.loads(raw)}).message


def parse_server_message(raw: str | bytes):
    """Parse and validate a server text frame. Used by tests and the CLI client."""
    return _ServerEnvelope.model_validate({"message": json.loads(raw)}).message


def dump(message: BaseModel) -> str:
    """Serialise any protocol message to a wire-ready JSON string."""
    return message.model_dump_json(by_alias=True, exclude_none=True)
