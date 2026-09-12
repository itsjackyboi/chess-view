#!/usr/bin/env python3
"""Accuracy harness for the vision pipeline -- the M2 go/no-go gate.

Reports **per-square and board-level accuracy separately**, because they are not
interchangeable and only the second one matters to a user. Per-square accuracy
flatters a model badly: 64 squares must all be right for one position to be right,
so 99% per square is a coin flip at board level, and 99.9% still misses one board in
sixteen. The table in docs/architecture.md is why this harness exists.

It also measures the pipeline under two different geometries:

* **calibrated** -- exact corners, as the client supplies after the user aligns the
  board. This is the production path and the number that should be quoted.
* **detected** -- corners found automatically, the fallback. Measured to quantify
  the cost of not having calibration, which is otherwise easy to forget about.

Usage:
    PYTHONPATH=services/vision .venv/bin/python services/vision/evaluate.py \
        --model models/square-classifier.onnx --boards 300
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from chessview_vision.board import (
    BOARD_PX,
    BoardNotFound,
    find_board,
    homography_for,
    square_crops,
)
from chessview_vision.classifier import INPUT_PX, SquareClassifier
from chessview_vision.synth import (
    NUM_CLASSES,
    PIECE_CLASSES,
    random_placement,
    render_board,
)


@dataclass
class Result:
    label: str
    boards: int = 0
    boards_skipped: int = 0
    square_correct: int = 0
    square_total: int = 0
    board_correct: int = 0
    class_correct: list[int] = field(default_factory=lambda: [0] * NUM_CLASSES)
    class_total: list[int] = field(default_factory=lambda: [0] * NUM_CLASSES)
    # Confidence reported on boards that turned out right vs wrong. If these
    # overlap, the confidence gate cannot separate good readings from bad ones,
    # which is the mechanism the whole "never show a wrong position" rule rests on.
    confidence_right: list[float] = field(default_factory=list)
    confidence_wrong: list[float] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)

    @property
    def square_accuracy(self) -> float:
        return self.square_correct / max(self.square_total, 1)

    @property
    def board_accuracy(self) -> float:
        return self.board_correct / max(self.boards, 1)

    def summary(self) -> dict:
        latency = np.array(self.latency_ms) if self.latency_ms else np.array([0.0])
        right = np.array(self.confidence_right) if self.confidence_right else np.array([])
        wrong = np.array(self.confidence_wrong) if self.confidence_wrong else np.array([])
        return {
            "label": self.label,
            "boards": self.boards,
            "boardsSkipped": self.boards_skipped,
            "squareAccuracy": round(self.square_accuracy, 5),
            "boardAccuracy": round(self.board_accuracy, 5),
            "latencyMsMedian": round(float(np.median(latency)), 2),
            "latencyMsP95": round(float(np.percentile(latency, 95)), 2),
            "confidenceOnCorrectBoards": round(float(right.mean()), 4) if right.size else None,
            "confidenceOnWrongBoards": round(float(wrong.mean()), 4) if wrong.size else None,
            "classRecall": {
                PIECE_CLASSES[i]: round(self.class_correct[i] / self.class_total[i], 4)
                for i in range(NUM_CLASSES)
                if self.class_total[i]
            },
        }


def evaluate(
    classifier: SquareClassifier,
    boards: int,
    *,
    seed: int,
    use_detection: bool,
) -> Result:
    result = Result("detected corners" if use_detection else "calibrated corners")
    rng = np.random.default_rng(seed)

    for _ in range(boards):
        rendered = render_board(random_placement(rng), rng)

        if use_detection:
            try:
                geometry = find_board(rendered.image)
            except BoardNotFound:
                # Counted, not silently dropped: a pipeline that finds no board is
                # failing, even if the squares it never classified cannot be wrong.
                result.boards_skipped += 1
                continue
            matrix = geometry.homography
        else:
            matrix = homography_for(rendered.corners)

        started = time.perf_counter()
        warped = cv2.warpPerspective(rendered.image, matrix, (BOARD_PX, BOARD_PX))
        predictions = classifier.predict(square_crops(warped, crop_px=INPUT_PX))
        result.latency_ms.append((time.perf_counter() - started) * 1000)

        truth = rendered.labels
        hits = predictions.labels == truth

        result.boards += 1
        result.square_correct += int(hits.sum())
        result.square_total += truth.size

        board_right = bool(hits.all())
        result.board_correct += int(board_right)
        (result.confidence_right if board_right else result.confidence_wrong).append(
            predictions.min_confidence
        )

        for cls in range(NUM_CLASSES):
            mask = truth == cls
            result.class_total[cls] += int(mask.sum())
            result.class_correct[cls] += int((hits & mask).sum())

    return result


def print_report(results: list[Result]) -> None:
    print("\n" + "=" * 72)
    print("ChessView vision accuracy  (synthetic held-out set)")
    print("=" * 72)

    for result in results:
        summary = result.summary()
        print(f"\n{summary['label']}   n={summary['boards']} boards", end="")
        if summary["boardsSkipped"]:
            print(f"  ({summary['boardsSkipped']} boards not located)", end="")
        print()
        print(f"  per-square accuracy   {summary['squareAccuracy'] * 100:7.3f}%")
        print(f"  board-level accuracy  {summary['boardAccuracy'] * 100:7.3f}%   <- what users see")
        print(
            f"  latency               {summary['latencyMsMedian']:6.1f}ms median"
            f"   {summary['latencyMsP95']:6.1f}ms p95"
        )
        if summary["confidenceOnCorrectBoards"] is not None:
            print(f"  confidence when right {summary['confidenceOnCorrectBoards']:.3f}")
        if summary["confidenceOnWrongBoards"] is not None:
            print(f"  confidence when wrong {summary['confidenceOnWrongBoards']:.3f}")

        weakest = sorted(summary["classRecall"].items(), key=lambda kv: kv[1])[:4]
        print("  weakest classes:      " + "  ".join(f"{k!r} {v * 100:.1f}%" for k, v in weakest))

    # Restating the compounding relationship next to the measured numbers, because
    # a good per-square figure reads as success until it is put in these terms.
    print("\n" + "-" * 72)
    square = results[0].square_accuracy
    print(
        f"A per-square accuracy of {square * 100:.3f}% implies "
        f"{square ** 64 * 100:.1f}% of boards fully correct if errors were independent."
    )
    print("-" * 72)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="models/square-classifier.onnx")
    parser.add_argument("--boards", type=int, default=300)
    # Disjoint from the training and validation seeds in train.py.
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--skip-detection", action="store_true")
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    classifier = SquareClassifier(args.model, threads=4)

    results = [evaluate(classifier, args.boards, seed=args.seed, use_detection=False)]
    if not args.skip_detection:
        results.append(
            evaluate(classifier, args.boards, seed=args.seed, use_detection=True)
        )

    print_report(results)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([r.summary() for r in results], indent=2) + "\n")
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
