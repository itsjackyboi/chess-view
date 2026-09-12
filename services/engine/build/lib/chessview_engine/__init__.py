"""Stockfish engine service: a warm process pool behind a UCI wrapper."""

from chessview_engine.analysis import report_to_protocol
from chessview_engine.config import EngineConfig
from chessview_engine.pool import EnginePool, EngineUnavailable, SessionEngine
from chessview_engine.uci import DepthReport, InfoLine, UciEngine, UciError, parse_info

__all__ = [
    "DepthReport",
    "EngineConfig",
    "EnginePool",
    "EngineUnavailable",
    "InfoLine",
    "SessionEngine",
    "UciEngine",
    "UciError",
    "parse_info",
    "report_to_protocol",
]
