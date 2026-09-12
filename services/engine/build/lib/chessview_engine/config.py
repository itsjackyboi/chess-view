"""Engine service configuration.

Defaults target the launch box in docs/architecture.md (8-core Ryzen) and are
overridable by environment variable so a deploy can be tuned without a rebuild.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


def _find_stockfish() -> str:
    """Locate a Stockfish binary, preferring an explicit override.

    Debian installs to /usr/games, which is not on a non-login shell's PATH, so
    `shutil.which` alone finds nothing on an otherwise working machine.
    """
    override = os.environ.get("CHESSVIEW_STOCKFISH")
    if override:
        return override
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in ("/usr/games/stockfish", "/usr/local/bin/stockfish"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "stockfish"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True, frozen=True)
class EngineConfig:
    binary: str = ""

    # One engine serves one active session, so the pool size is the concurrent
    # session ceiling. threads * size should land near the host's core count:
    # oversubscribing makes every session slower rather than serving more of them.
    pool_size: int = 4
    threads: int = 2
    hash_mb: int = 256
    multipv: int = 3

    # Stop at whichever comes first. The movetime cap is what protects the latency
    # budget -- depth alone is unbounded in time on complex positions, which are
    # exactly the ones where a user is watching the bar.
    depth: int = 20
    movetime_ms: int = 1200

    @classmethod
    def from_env(cls) -> "EngineConfig":
        return cls(
            binary=_find_stockfish(),
            pool_size=_env_int("CHESSVIEW_ENGINE_POOL_SIZE", 4),
            threads=_env_int("CHESSVIEW_ENGINE_THREADS", 2),
            hash_mb=_env_int("CHESSVIEW_ENGINE_HASH_MB", 256),
            multipv=_env_int("CHESSVIEW_ENGINE_MULTIPV", 3),
            depth=_env_int("CHESSVIEW_ENGINE_DEPTH", 20),
            movetime_ms=_env_int("CHESSVIEW_ENGINE_MOVETIME_MS", 1200),
        )

    def __post_init__(self) -> None:
        if not self.binary:
            object.__setattr__(self, "binary", _find_stockfish())
