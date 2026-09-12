#!/usr/bin/env python3
"""Drive concurrent sessions against a running vision service.

The test suite covers one session at a time. What it cannot tell you is whether the
engine pool behaves when every lease is taken -- whether sessions queue, time out,
or interfere with each other, and what the latency looks like under contention.

Usage:
    make run-vision   # in another terminal
    PYTHONPATH=services/vision .venv/bin/python services/vision/loadtest.py \
        --sessions 8 --moves 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import chess
import cv2
import numpy as np
import websockets

from chessview_vision.board import BOARD_PX, homography_for
from chessview_vision.synth import render_board
from chessview_protocol import (
    PROTOCOL_VERSION,
    ClientInfo,
    FrameHeader,
    Hello,
    dump,
    encode_frame,
)

CALIBRATE = json.dumps({
    "type": "calibrate",
    "corners": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
    "orientation": "white_bottom",
})


def build_game_frames(moves: int) -> list[bytes]:
    """Render a short game as rectified board JPEGs.

    Real images rather than placeholder bytes: a load test that sends undecodable
    frames measures the transport and nothing else, and inference is most of the
    per-frame cost.
    """
    rng = np.random.default_rng(0)
    board = chess.Board()
    frames: list[bytes] = []

    for _ in range(moves + 1):
        rendered = render_board(board.board_fen(), rng, perspective=False)
        rectified = cv2.warpPerspective(
            rendered.image, homography_for(rendered.corners), (BOARD_PX, BOARD_PX)
        )
        ok, buffer = cv2.imencode(".jpg", rectified, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise SystemExit("could not encode a frame")
        frames.append(buffer.tobytes())

        legal = list(board.legal_moves)
        if not legal:
            break
        board.push(legal[int(rng.integers(len(legal)))])

    return frames


class SessionResult:
    def __init__(self) -> None:
        self.positions = 0
        self.evaluations = 0
        self.first_eval_ms: list[float] = []
        self.error: str | None = None
        self.refused: bool = False


async def run_session(
    url: str, moves: int, frames_per_move: int, frames: list[bytes]
) -> SessionResult:
    result = SessionResult()
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            await ws.send(dump(Hello(
                protocolVersion=PROTOCOL_VERSION,
                client=ClientInfo(platform="test", appVersion="loadtest"),
            )))
            ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if ready["type"] == "error":
                # Being refused is a legitimate outcome under load, not a failure --
                # the service telling a client it is full is the correct behaviour.
                result.refused = True
                result.error = ready.get("message")
                return result

            await ws.send(CALIBRATE)

            sent_at: float | None = None
            seen_eval_for_move = True

            async def pump() -> None:
                nonlocal seen_eval_for_move
                while True:
                    try:
                        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.05))
                    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                        return
                    if message["type"] == "position":
                        result.positions += 1
                    elif message["type"] == "eval":
                        result.evaluations += 1
                        if sent_at is not None and not seen_eval_for_move:
                            result.first_eval_ms.append((time.perf_counter() - sent_at) * 1000)
                            seen_eval_for_move = True

            seq = 0
            for index in range(moves * frames_per_move + frames_per_move):
                seq += 1
                # Hold each position for frames_per_move frames, as a real client
                # does while the stability gate confirms a move.
                frame = frames[min(index // frames_per_move, len(frames) - 1)]
                if seq % frames_per_move == 0:
                    sent_at = time.perf_counter()
                    seen_eval_for_move = False
                await ws.send(encode_frame(
                    FrameHeader(seq=seq, captureTs=time.time() * 1000, motion=True), frame
                ))
                await asyncio.sleep(0.2)  # the client's 5 fps motion burst
                await pump()

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                await pump()

    except Exception as exc:  # noqa: BLE001 - a load test reports, it does not raise
        result.error = f"{type(exc).__name__}: {exc}"
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/v1/session")
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--moves", type=int, default=6)
    parser.add_argument("--frames-per-move", type=int, default=4)
    args = parser.parse_args()

    frames = build_game_frames(args.moves)
    print(f"rendered {len(frames)} board frames "
          f"({sum(len(f) for f in frames) // len(frames) // 1024} KB each)")
    print(f"opening {args.sessions} concurrent sessions against {args.url}\n")

    started = time.perf_counter()
    results = await asyncio.gather(
        *(run_session(args.url, args.moves, args.frames_per_move, frames)
          for _ in range(args.sessions))
    )
    elapsed = time.perf_counter() - started

    served = [r for r in results if not r.refused and r.error is None]
    refused = [r for r in results if r.refused]
    failed = [r for r in results if r.error and not r.refused]

    print(f"served   {len(served)}/{args.sessions}")
    print(f"refused  {len(refused)}  (the service declining work is correct behaviour)")
    print(f"failed   {len(failed)}")
    for result in failed[:3]:
        print(f"    {result.error}")

    latencies = [ms for r in served for ms in r.first_eval_ms]
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(f"\nmove -> first evaluation, under {len(served)} concurrent sessions:")
        print(f"  median {statistics.median(latencies):7.1f}ms")
        print(f"  p95    {p95:7.1f}ms")
        print(f"  max    {max(latencies):7.1f}ms")
        print(f"  n      {len(latencies)}")

    print(f"\npositions {sum(r.positions for r in served)}"
          f"   evaluations {sum(r.evaluations for r in served)}"
          f"   wall {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
