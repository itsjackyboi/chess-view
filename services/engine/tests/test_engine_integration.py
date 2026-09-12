"""Integration tests driving a real Stockfish process.

Parsing can be unit-tested, but the behaviour that actually matters -- that
evaluations arrive progressively, that a cancelled search leaves the process
reusable, that scores come out from white's point of view -- only shows up against
a real engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from chessview_protocol import Eval

from chessview_engine.pool import EnginePool, EngineUnavailable
from chessview_engine.uci import UciEngine

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
# Black to move, but white is overwhelmingly winning (up a queen and a rook).
# The sharpest test of score normalisation: from the side to move this reads as a
# large negative, and it must reach the client as a large positive.
WHITE_WINNING_BLACK_TO_MOVE = "4k3/8/8/8/8/8/4QR2/4K3 b - - 0 1"
# Black to move, mate in one: ...Qg2# (the queen is protected by the king on f2).
BLACK_MATES_IN_ONE = "6q1/8/8/8/8/8/5k2/7K b - - 0 1"


async def _collect(pool: EnginePool, fen: str) -> list[Eval]:
    updates: list[Eval] = []
    done = asyncio.Event()

    async def on_update(ev: Eval) -> None:
        updates.append(ev)
        if ev.final:
            done.set()

    async with pool.lease() as session:
        await session.analyse(fen, on_update)
        await asyncio.wait_for(done.wait(), timeout=30)
    return updates


async def test_analysis_streams_progressively_then_finalises(pool: EnginePool):
    updates = await _collect(pool, START)

    assert len(updates) >= 2, "expected several progressive updates, not one final one"
    assert [u.depth for u in updates] == sorted(u.depth for u in updates)
    assert all(not u.final for u in updates[:-1])
    assert updates[-1].final
    assert all(u.fen == START for u in updates)


async def test_returns_multipv_lines_with_san(pool: EnginePool):
    final = (await _collect(pool, START))[-1]

    assert len(final.lines) >= 2, "MultiPV should return more than one candidate"
    assert [line.multipv for line in final.lines] == list(
        range(1, len(final.lines) + 1)
    )
    best = final.lines[0]
    assert best.pv and best.san
    assert best.san[0][0].isalpha()


async def test_scores_reach_the_client_from_whites_perspective(pool: EnginePool):
    """Black to move in a position white is winning must report a positive score."""
    final = (await _collect(pool, WHITE_WINNING_BLACK_TO_MOVE))[-1]
    best = final.lines[0]

    if best.score_mate is not None:
        assert best.score_mate > 0, "white mating should be a positive mate score"
    else:
        assert best.score_cp is not None and best.score_cp > 300


async def test_mate_for_black_is_reported_negative(pool: EnginePool):
    final = (await _collect(pool, BLACK_MATES_IN_ONE))[-1]
    best = final.lines[0]

    assert best.score_mate is not None, "expected a forced mate to be found"
    assert best.score_mate < 0, "black mating must be negative from white's view"


async def test_a_new_position_cancels_the_search_in_flight(engine_config):
    """The core real-time behaviour: a move abandons analysis of the old position.

    Uses a deliberately long search so the first analysis is provably still running
    when the second arrives -- at the shallow depth the other tests use, the start
    position finishes before the cancellation lands and the test proves nothing.
    """
    slow = replace(engine_config, depth=99, movetime_ms=60_000)
    pool = EnginePool(slow)
    await pool.start()
    try:
        first: list[Eval] = []
        second: list[Eval] = []
        got_second = asyncio.Event()

        async def on_first(ev: Eval) -> None:
            first.append(ev)

        async def on_second(ev: Eval) -> None:
            second.append(ev)
            got_second.set()

        async with pool.lease() as session:
            await session.analyse(START, on_first)
            await asyncio.sleep(0.5)
            assert first, "the first search should have produced updates by now"
            assert not any(ev.final for ev in first), "first search finished too soon"

            await session.analyse(WHITE_WINNING_BLACK_TO_MOVE, on_second)
            await asyncio.wait_for(got_second.wait(), timeout=30)
            await session.cancel()

        assert all(ev.fen == START for ev in first), "no cross-talk between searches"
        assert not any(ev.final for ev in first), "cancelled search must not finalise"
        assert all(ev.fen == WHITE_WINNING_BLACK_TO_MOVE for ev in second)
    finally:
        await pool.stop()


async def test_engine_is_reusable_after_cancellation(pool: EnginePool):
    """Abandoning a search must leave the process healthy, not wedged mid-search."""
    async with pool.lease() as session:
        await session.analyse(START, lambda ev: asyncio.sleep(0))
        await asyncio.sleep(0.15)
        await session.cancel()

    updates = await _collect(pool, START)
    assert updates and updates[-1].final


# --------------------------------------------------------------------------
# Pool behaviour
# --------------------------------------------------------------------------


async def test_leases_are_returned_to_the_pool(pool: EnginePool):
    assert pool.available == pool.size
    async with pool.lease():
        assert pool.available == pool.size - 1
    assert pool.available == pool.size


async def test_exhausted_pool_reports_capacity_rather_than_hanging(pool: EnginePool):
    """A client on a full pool is told so -- it never sits on a frozen evaluation."""
    async with pool.lease(), pool.lease():
        assert pool.available == 0
        with pytest.raises(EngineUnavailable, match="busy"):
            async with pool.lease(timeout=0.2):
                pass


async def test_a_dead_engine_is_replaced_on_return(pool: EnginePool):
    size_before = pool.size
    async with pool.lease() as session:
        engine: UciEngine = session._engine  # noqa: SLF001 - asserting recovery
        await engine.stop()

    assert pool.size == size_before
    assert pool.available == size_before
    updates = await _collect(pool, START)
    assert updates[-1].final


async def test_unstarted_pool_is_refused_explicitly(engine_config):
    with pytest.raises(EngineUnavailable, match="not been started"):
        async with EnginePool(engine_config).lease():
            pass
