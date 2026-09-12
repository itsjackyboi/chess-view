"""Rate limiting.

Sessions are anonymous, so this module is the only thing standing between one
client and the whole engine pool. An engine serves one session at a time, so a
handful of connections from one source can deny service to everyone else.
"""

from __future__ import annotations

import json

import pytest
from chessview_protocol import PROTOCOL_VERSION, ClientInfo, Hello, dump
from fastapi.testclient import TestClient

from chessview_vision.app import create_app
from chessview_vision.detector import Detection, ScriptedDetector
from chessview_vision.limits import ClientLimiter, FrameLimiter, LimitConfig, TokenBucket
from chessview_vision.testing import FakePool


class TestTokenBucket:
    def test_allows_a_full_burst_immediately(self):
        bucket = TokenBucket(rate=10, capacity=5, now=0.0)
        assert [bucket.allow(now=0.0) for _ in range(5)] == [True] * 5

    def test_refuses_once_the_burst_is_spent(self):
        bucket = TokenBucket(rate=10, capacity=3, now=0.0)
        for _ in range(3):
            bucket.allow(now=0.0)
        assert bucket.allow(now=0.0) is False

    def test_refills_over_time(self):
        bucket = TokenBucket(rate=10, capacity=3, now=0.0)
        for _ in range(3):
            bucket.allow(now=0.0)
        # 10 per second, so 0.1s buys exactly one.
        assert bucket.allow(now=0.1) is True
        assert bucket.allow(now=0.1) is False

    def test_does_not_bank_more_than_the_burst(self):
        """A window-based limiter would let a client send two windows back to back.

        Capping accumulated tokens is what stops a long idle period becoming a
        flood, which is the spike the limit exists to prevent.
        """
        bucket = TokenBucket(rate=10, capacity=3, now=0.0)
        assert [bucket.allow(now=1000.0) for _ in range(4)] == [True, True, True, False]

    def test_tolerates_a_clock_that_goes_backwards(self):
        bucket = TokenBucket(rate=10, capacity=2, now=100.0)
        bucket.allow(now=100.0)
        # Negative elapsed time must not mint tokens.
        assert bucket.allow(now=50.0) is True
        assert bucket.allow(now=50.0) is False


class TestClientLimiter:
    def test_allows_up_to_the_cap(self):
        limiter = ClientLimiter(LimitConfig(max_sessions_per_client=2))
        assert limiter.try_acquire("1.2.3.4", "a") is True
        assert limiter.try_acquire("1.2.3.4", "b") is True
        assert limiter.try_acquire("1.2.3.4", "c") is False

    def test_releasing_frees_a_slot(self):
        limiter = ClientLimiter(LimitConfig(max_sessions_per_client=1))
        limiter.try_acquire("1.2.3.4", "a")
        limiter.release("1.2.3.4", "a")
        assert limiter.try_acquire("1.2.3.4", "b") is True

    def test_clients_are_tracked_independently(self):
        limiter = ClientLimiter(LimitConfig(max_sessions_per_client=1))
        assert limiter.try_acquire("1.2.3.4", "a") is True
        assert limiter.try_acquire("5.6.7.8", "b") is True

    def test_releasing_the_last_session_forgets_the_client(self):
        """Otherwise a long-running process accumulates an entry per address seen."""
        limiter = ClientLimiter()
        limiter.try_acquire("1.2.3.4", "a")
        limiter.release("1.2.3.4", "a")
        assert limiter.active_clients == 0

    def test_releasing_an_unknown_session_is_harmless(self):
        ClientLimiter().release("nobody", "nothing")

    def test_counts_rejections_for_monitoring(self):
        limiter = ClientLimiter(LimitConfig(max_sessions_per_client=1))
        limiter.try_acquire("1.2.3.4", "a")
        limiter.try_acquire("1.2.3.4", "b")
        assert limiter.rejections == 1


class TestFrameLimiter:
    def test_permits_a_normal_motion_burst(self):
        # The client bursts to 5 fps on motion; that must never be throttled.
        limiter = FrameLimiter(LimitConfig(max_frames_per_second=12, frame_burst=24))
        allowed = [limiter.allow(now=index * 0.2) for index in range(20)]
        assert all(allowed)

    def test_throttles_a_flood(self):
        limiter = FrameLimiter(LimitConfig(max_frames_per_second=12, frame_burst=10))
        results = [limiter.allow(now=0.0) for _ in range(40)]
        assert sum(results) == 10
        assert limiter.dropped == 30

    def test_recovers_after_the_flood_stops(self):
        limiter = FrameLimiter(LimitConfig(max_frames_per_second=12, frame_burst=5))
        for _ in range(10):
            limiter.allow(now=0.0)
        assert limiter.allow(now=2.0) is True

    def test_reports_the_observed_rate(self):
        limiter = FrameLimiter()
        for index in range(11):
            limiter.allow(now=index * 0.1)
        assert limiter.observed_fps == pytest.approx(10.0, rel=0.05)


class TestTransportEnforcement:
    @pytest.fixture
    def client(self):
        app = create_app(
            pool=FakePool(),
            detector_factory=lambda: ScriptedDetector(
                [Detection("8/8/8/8/8/8/8/8", 0.9)], loop=True
            ),
            limits=LimitConfig(max_sessions_per_client=2),
        )
        with TestClient(app) as test_client:
            yield test_client

    @staticmethod
    def _hello() -> str:
        return dump(
            Hello(
                protocolVersion=PROTOCOL_VERSION,
                client=ClientInfo(platform="test", appVersion="0.1.0"),
            )
        )

    def test_a_client_opening_too_many_sessions_is_refused(self, client: TestClient):
        """One client must not be able to exhaust the pool for everyone else."""
        with client.websocket_connect("/v1/session") as first:
            first.send_text(self._hello())
            assert first.receive_json()["type"] == "sessionReady"

            with client.websocket_connect("/v1/session") as second:
                second.send_text(self._hello())
                assert second.receive_json()["type"] == "sessionReady"

                with client.websocket_connect("/v1/session") as third:
                    third.send_text(self._hello())
                    refused = third.receive_json()
                    assert refused["type"] == "error"
                    assert refused["code"] == "rate_limited"
                    assert refused["fatal"] is True

    def test_a_slot_frees_when_a_session_closes(self, client: TestClient):
        for _ in range(4):
            with client.websocket_connect("/v1/session") as ws:
                ws.send_text(self._hello())
                assert ws.receive_json()["type"] == "sessionReady"

    def test_metrics_report_detection_health(self, client: TestClient):
        with client.websocket_connect("/v1/session") as ws:
            ws.send_text(self._hello())
            ws.receive_json()

        body = client.get("/metrics").json()
        assert body["sessions"]["started"] >= 1
        # The numbers that actually say whether detection is working.
        assert "resyncRate" in body["detection"]
        assert "confidenceP05" in body["detection"]
        assert "activeSessions" in body["limits"]

    def test_health_still_reports_which_detector_is_live(self, client: TestClient):
        # A deploy on the stub reports scripted positions; that must be visible.
        assert client.get("/health").json()["detector"] == "stub"
