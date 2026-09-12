from __future__ import annotations

import pytest

from chessview_vision.testing import FakeEngine, Recorder


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()
