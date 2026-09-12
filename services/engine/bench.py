#!/usr/bin/env python3
"""Measure engine latency against the targets in docs/architecture.md.

The number that matters is **time to first evaluation**, not time to final depth.
The architecture streams progressively precisely because those two differ by more
than an order of magnitude, and only the first one is inside the latency budget.

Usage:  .venv/bin/python services/engine/bench.py [--repeat N]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from chessview_protocol import Eval

from chessview_engine.config import EngineConfig
from chessview_engine.pool import EnginePool

# A spread of game phases: openings hit the book-like shallow cases, the endgame and
# the tactical middlegame are where search time actually goes.
POSITIONS = [
    ("opening", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("italian", "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"),
    ("middlegame", "r2q1rk1/pp1nbppp/2p1pn2/3p4/2PP4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 10"),
    ("endgame", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
]


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


async def main(repeat: int) -> None:
    config = EngineConfig.from_env()
    pool = EnginePool(config)
    await pool.start()

    firsts: list[float] = []
    finals: list[float] = []
    depths: list[int] = []

    try:
        async with pool.lease() as session:
            for _ in range(repeat):
                for _name, fen in POSITIONS:
                    started = time.perf_counter()
                    first: float | None = None
                    final_depth = 0
                    done = asyncio.Event()

                    async def on_update(ev: Eval) -> None:
                        nonlocal first, final_depth
                        if first is None:
                            first = time.perf_counter() - started
                        final_depth = ev.depth
                        if ev.final:
                            done.set()

                    await session.analyse(fen, on_update)
                    await asyncio.wait_for(done.wait(), timeout=60)

                    assert first is not None
                    firsts.append(first * 1000)
                    finals.append((time.perf_counter() - started) * 1000)
                    depths.append(final_depth)
    finally:
        await pool.stop()

    print(f"engine:     {pool.engine_name}")
    print(f"settings:   threads={config.threads} hash={config.hash_mb}MB "
          f"multipv={config.multipv} depth={config.depth} movetime={config.movetime_ms}ms")
    print(f"positions:  {len(firsts)}\n")
    for label, values in (("first eval", firsts), ("final eval", finals)):
        print(f"{label} ms:  median {statistics.median(values):8.1f}"
              f"   p95 {_percentile(values, 0.95):8.1f}"
              f"   max {max(values):8.1f}")
    print(f"final depth:   median {statistics.median(depths):8.1f}"
          f"   min {min(depths):8d}   max {max(depths):8d}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    main_args = parser.parse_args()
    asyncio.run(main(main_args.repeat))
