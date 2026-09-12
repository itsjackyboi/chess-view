"""Vision service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(slots=True, frozen=True)
class VisionConfig:
    # Consecutive agreeing observations before a position is committed. Three at the
    # client's 5 fps motion burst costs ~600 ms, which is what the end-to-end budget
    # in docs/architecture.md leaves for confirmation.
    stability_frames: int = 3

    # Observations below this are discarded rather than tracked. The product rule is
    # to say "unclear" instead of showing analysis for a position that may be wrong.
    min_confidence: float = 0.6

    # Consecutive stable-but-unexplainable observations before accepting the board
    # as a fresh position. Recovers from a missed move instead of wedging forever.
    resync_frames: int = 8

    # How long a client may be disconnected before its session state is discarded.
    # Long enough to survive a tunnel or a backgrounded app; short enough that
    # abandoned sessions release their engine lease.
    session_ttl_seconds: int = 120

    # Path to the ONNX square classifier. When absent the service falls back to the
    # stub detector and says so loudly at startup, rather than appearing to work
    # while reporting scripted positions.
    model_path: str = ""

    # Whether the client sends an already-rectified board crop. True is the target
    # design (see docs/architecture.md); False makes the server localise the board
    # itself, which works but is less accurate and sends whole frames.
    expect_rectified: bool = False

    @property
    def model(self) -> Path | None:
        path = Path(self.model_path) if self.model_path else None
        return path if path and path.is_file() else None

    @classmethod
    def from_env(cls) -> "VisionConfig":
        return cls(
            stability_frames=_env_int("CHESSVIEW_STABILITY_FRAMES", 3),
            min_confidence=_env_float("CHESSVIEW_MIN_CONFIDENCE", 0.6),
            resync_frames=_env_int("CHESSVIEW_RESYNC_FRAMES", 8),
            session_ttl_seconds=_env_int("CHESSVIEW_SESSION_TTL", 120),
            model_path=os.environ.get("CHESSVIEW_MODEL", ""),
            expect_rectified=os.environ.get("CHESSVIEW_EXPECT_RECTIFIED", "0") == "1",
        )
