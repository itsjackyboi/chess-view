"""Graceful shutdown.

Without draining, a deploy tears engines out from under active sessions. The client
sees an unexplained drop, which looks identical to a network failure, so it
reconnects -- to a server that is still going down.
"""

from __future__ import annotations

import asyncio

import pytest
from chessview_protocol import PROTOCOL_VERSION, ClientInfo, Hello, dump
from fastapi.testclient import TestClient

from chessview_vision.app import Draining, create_app
from chessview_vision.detector import Detection, ScriptedDetector
from chessview_vision.testing import FakePool


def hello() -> str:
    return dump(
        Hello(
            protocolVersion=PROTOCOL_VERSION,
            client=ClientInfo(platform="test", appVersion="0.1.0"),
        )
    )


class TestDrainingState:
    def test_starts_accepting_traffic(self):
        assert Draining().is_draining is False

    def test_counts_live_sessions(self):
        draining = Draining()
        draining.enter()
        draining.enter()
        assert draining.live_sessions == 2
        draining.leave()
        assert draining.live_sessions == 1

    def test_leave_never_goes_negative(self):
        # An unbalanced leave would otherwise wedge the drain wait forever.
        draining = Draining()
        draining.leave()
        assert draining.live_sessions == 0

    async def test_waiting_with_no_sessions_returns_immediately(self):
        assert await Draining().wait_for_sessions(timeout=0.01) is True

    async def test_waiting_completes_once_the_last_session_leaves(self):
        draining = Draining()
        draining.enter()

        async def finish() -> None:
            await asyncio.sleep(0.02)
            draining.leave()

        asyncio.create_task(finish())
        assert await draining.wait_for_sessions(timeout=2.0) is True

    async def test_waiting_gives_up_so_a_deploy_is_not_held_hostage(self):
        """One stuck connection must not block a deploy indefinitely."""
        draining = Draining()
        draining.enter()
        assert await draining.wait_for_sessions(timeout=0.05) is False


class TestDrainingTransport:
    @pytest.fixture
    def app(self):
        return create_app(
            pool=FakePool(),
            detector_factory=lambda: ScriptedDetector(
                [Detection("8/8/8/8/8/8/8/8", 0.9)], loop=True
            ),
        )

    def test_a_draining_server_refuses_new_sessions_with_a_reason(self, app):
        with TestClient(app) as client:
            app.state.pool  # ensure lifespan ran
            _draining_flag(app).begin()

            with client.websocket_connect("/v1/session") as ws:
                error = ws.receive_json()
                assert error["type"] == "error"
                assert error["fatal"] is True
                assert "shutting down" in error["message"]

    def test_health_reports_draining_so_a_balancer_can_act(self, app):
        """Distinct from at_capacity: capacity is temporary and still wants traffic,
        a draining instance is going away and should leave the rotation."""
        with TestClient(app) as client:
            assert client.get("/health").json()["status"] == "ok"
            _draining_flag(app).begin()
            assert client.get("/health").json()["status"] == "draining"

    def test_normal_sessions_are_unaffected_before_shutdown(self, app):
        with TestClient(app) as client:
            with client.websocket_connect("/v1/session") as ws:
                ws.send_text(hello())
                assert ws.receive_json()["type"] == "sessionReady"


def _draining_flag(app) -> Draining:
    """Reach the Draining instance the app closed over.

    It is deliberately not on app.state -- nothing outside the app should be able to
    put the service into shutdown -- so the test digs it out of the route closure.
    """
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        closure = getattr(endpoint, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, Draining):
                return cell.cell_contents
    raise AssertionError("no Draining instance found on the app")
