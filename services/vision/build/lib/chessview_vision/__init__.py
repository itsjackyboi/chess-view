"""Vision service: WebSocket transport, session state, and board tracking."""

from chessview_vision.config import VisionConfig
from chessview_vision.detector import Detection, Detector, ScriptedDetector, StubDetector
from chessview_vision.registry import SessionRegistry
from chessview_vision.session import Session
from chessview_vision.tracker import BoardTracker, Outcome, TrackerResult

__all__ = [
    "BoardTracker",
    "Detection",
    "Detector",
    "Outcome",
    "ScriptedDetector",
    "Session",
    "SessionRegistry",
    "StubDetector",
    "TrackerResult",
    "VisionConfig",
]
