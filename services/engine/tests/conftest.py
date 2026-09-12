from __future__ import annotations

import pytest
import pytest_asyncio

from chessview_engine.config import EngineConfig
from chessview_engine.pool import EnginePool

_config = EngineConfig.from_env()


@pytest.fixture(scope="session")
def engine_config() -> EngineConfig:
    """Small and shallow: tests assert behaviour, not playing strength."""
    return EngineConfig(
        binary=_config.binary,
        pool_size=2,
        threads=1,
        hash_mb=16,
        multipv=3,
        depth=12,
        movetime_ms=800,
    )


@pytest_asyncio.fixture
async def pool(engine_config: EngineConfig):
    pool = EnginePool(engine_config)
    await pool.start()
    try:
        yield pool
    finally:
        await pool.stop()
