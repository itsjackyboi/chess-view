#!/usr/bin/env python3
"""Run the whole pipeline over a single image and print what it found.

The quickest way to see the system work end to end without a phone: image in,
position and engine analysis out. Useful for checking a new model against real
photographs, which is exactly where the synthetic training set stops predicting
anything.

Usage:
    PYTHONPATH=services/vision .venv/bin/python services/vision/demo.py board.jpg
    ... --corners 120,80 640,95 660,610 100,595   # skip detection, as calibration does
    ... --no-engine                               # position only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import chess
import cv2
import numpy as np

from chessview_engine.config import EngineConfig
from chessview_engine.pool import EnginePool, EngineUnavailable
from chessview_protocol import Eval
from chessview_vision.board import BOARD_PX, BoardNotFound, find_board, geometry_from_corners
from chessview_vision.classifier import ModelUnavailable, SquareClassifier
from chessview_vision.cnn_detector import CnnDetector

DEFAULT_MODEL = "models/square-classifier.onnx"


def parse_corners(values: list[str] | None) -> np.ndarray | None:
    if not values:
        return None
    if len(values) != 4:
        raise SystemExit("--corners needs exactly four x,y pairs")
    try:
        points = [tuple(float(part) for part in value.split(",")) for value in values]
    except ValueError as exc:
        raise SystemExit(f"could not parse --corners: {exc}") from exc
    if any(len(point) != 2 for point in points):
        raise SystemExit("each corner must be 'x,y'")
    return np.array(points, dtype=np.float32)


def render_ascii(board: chess.Board) -> str:
    """The position as text, so the output can be checked at a glance."""
    lines = []
    for rank in range(8, 0, -1):
        row = [f"{rank} "]
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank - 1))
            row.append(piece.symbol() if piece else ".")
            row.append(" ")
        lines.append("".join(row))
    lines.append("  a b c d e f g h")
    return "\n".join(lines)


async def analyse(fen: str, config: EngineConfig) -> Eval | None:
    pool = EnginePool(config)
    try:
        await pool.start()
    except EngineUnavailable as exc:
        print(f"  (no engine available: {exc})")
        return None

    latest: Eval | None = None
    done = asyncio.Event()

    async def on_update(ev: Eval) -> None:
        nonlocal latest
        latest = ev
        if ev.final:
            done.set()

    try:
        async with pool.lease() as session:
            await session.analyse(fen, on_update)
            await asyncio.wait_for(done.wait(), timeout=30)
    except (asyncio.TimeoutError, EngineUnavailable):
        pass
    finally:
        await pool.stop()

    return latest


def describe(line, board: chess.Board) -> str:
    if line.score_mate is not None:
        score = f"mate in {abs(line.score_mate)} for {'White' if line.score_mate > 0 else 'Black'}"
    elif line.score_cp is not None:
        pawns = line.score_cp / 100
        score = f"{pawns:+.2f}"
    else:
        score = "?"
    moves = " ".join(line.san[:6]) if line.san else " ".join(line.pv[:6])
    return f"{score:>18}   {moves}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--corners", nargs="*", help="four 'x,y' pairs, as calibration supplies")
    parser.add_argument("--turn", choices=["w", "b"], default="w")
    parser.add_argument("--no-engine", action="store_true")
    parser.add_argument("--save-rectified", type=Path, help="write the flattened board here")
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"no such image: {args.image}")

    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(f"could not decode {args.image}")

    try:
        classifier = SquareClassifier(args.model)
    except ModelUnavailable as exc:
        raise SystemExit(f"{exc}\nTrain one with services/vision/training/train.py") from exc

    corners = parse_corners(args.corners)
    detector = CnnDetector(classifier, expect_rectified=False)

    if corners is not None:
        detector.set_corners(corners)
        source = "supplied corners"
    else:
        try:
            source = f"detected corners ({find_board(image).source})"
        except BoardNotFound:
            raise SystemExit(
                "no board found in the image.\n"
                "Pass --corners to supply them, as the app's calibration does."
            ) from None

    started = time.perf_counter()
    detection = asyncio.run(detector.detect(_encode(image)))
    elapsed_ms = (time.perf_counter() - started) * 1000

    if detection is None:
        raise SystemExit("the detector could not read a board from this image")

    if args.save_rectified:
        geometry = geometry_from_corners(corners) if corners is not None else find_board(image)
        cv2.imwrite(str(args.save_rectified), geometry.warp(image, BOARD_PX))
        print(f"rectified board written to {args.save_rectified}")

    board = chess.Board(None)
    try:
        board.set_board_fen(detection.placement)
    except ValueError as exc:
        raise SystemExit(f"the detector produced an unreadable position: {exc}") from exc
    board.turn = chess.WHITE if args.turn == "w" else chess.BLACK

    print(f"\n{render_ascii(board)}\n")
    print(f"  source       {source}")
    print(f"  FEN          {board.fen()}")
    print(f"  confidence   {detection.confidence:.3f}  (weakest square)")
    print(f"  detection    {elapsed_ms:.0f}ms")

    # Flagged explicitly: a low weakest-square score is the signal that this
    # position may be wrong, and the app would show "unclear" rather than analysis.
    if detection.confidence < 0.6:
        print("\n  ! Low confidence. The app would say 'unclear' rather than analyse this.")

    weak = sorted(detection.squares.items(), key=lambda item: item[1])[:3]
    names = ", ".join(f"{chess.square_name(_to_square(i))} {v:.2f}" for i, v in weak)
    print(f"  least sure   {names}")

    if args.no_engine:
        return

    if not board.is_valid():
        print("\n  ! Not a legal position, so it cannot be analysed.")
        print("    The app would keep the previous position and report 'unclear'.")
        return

    print("\n  analysing...")
    evaluation = asyncio.run(analyse(board.fen(), EngineConfig.from_env()))
    if evaluation is None:
        return

    print(f"\n  depth {evaluation.depth}")
    for line in evaluation.lines:
        print(f"  {describe(line, board)}")


def _to_square(index: int) -> int:
    """Crop index (0 = a8) to a python-chess square number (0 = a1)."""
    row, col = divmod(index, 8)
    return chess.square(col, 7 - row)


def _encode(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise SystemExit("could not re-encode the image")
    return buffer.tobytes()


if __name__ == "__main__":
    sys.exit(main())
