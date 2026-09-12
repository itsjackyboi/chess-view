#!/usr/bin/env python3
"""Train the square classifier and export it to ONNX.

Usage:
    PYTHONPATH=services/vision .venv/bin/python services/vision/training/train.py \
        --train-boards 1200 --epochs 8 --out models/square-classifier.onnx

Reports **per-square and board-level accuracy separately**, because only the second
one matters to a user and the first one flatters the model badly: 99% per square is
a coin flip at board level. See docs/architecture.md.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from chessview_vision.synth import NUM_CLASSES, PIECE_CLASSES
from training.augment import augment, normalise
from training.dataset import build_cache
from training.model import SquareNet, export_onnx


def to_tensor(crops: np.ndarray) -> torch.Tensor:
    """Cached uint8 crops (N,H,W,C) to a float NCHW tensor in [0,255]."""
    return torch.from_numpy(crops).permute(0, 3, 1, 2).contiguous().float()


def batches(count: int, size: int, *, shuffle: bool, generator=None):
    order = torch.randperm(count, generator=generator) if shuffle else torch.arange(count)
    for start in range(0, count - (count % size if shuffle else 0), size):
        chunk = order[start:start + size]
        if len(chunk):
            yield chunk


def evaluate(
    model: nn.Module, crops: torch.Tensor, labels: torch.Tensor, device: torch.device
) -> dict:
    """Per-square accuracy, board-level accuracy, and per-class recall.

    Evaluated in cache order without shuffling, because the board-level metric
    depends on each consecutive run of 64 rows being one board.
    """
    model.eval()

    square_correct = square_total = 0
    boards_correct = boards_total = 0
    class_correct = np.zeros(NUM_CLASSES, dtype=np.int64)
    class_total = np.zeros(NUM_CLASSES, dtype=np.int64)

    with torch.no_grad():
        for index in batches(len(crops), 64 * 16, shuffle=False):
            batch = normalise(crops[index].to(device))
            target = labels[index].to(device)
            hits = model(batch).argmax(dim=1) == target

            square_correct += int(hits.sum())
            square_total += target.numel()

            # A board counts only if every one of its squares is right.
            per_board = hits.view(-1, 64).all(dim=1)
            boards_correct += int(per_board.sum())
            boards_total += per_board.numel()

            for cls in range(NUM_CLASSES):
                mask = target == cls
                class_total[cls] += int(mask.sum())
                class_correct[cls] += int((hits & mask).sum())

    recall = np.zeros(NUM_CLASSES, dtype=float)
    np.divide(class_correct, class_total, out=recall, where=class_total > 0)

    return {
        "square_accuracy": square_correct / max(square_total, 1),
        "board_accuracy": boards_correct / max(boards_total, 1),
        "class_recall": {PIECE_CLASSES[i]: recall[i] for i in range(NUM_CLASSES)},
        "class_support": {PIECE_CLASSES[i]: int(class_total[i]) for i in range(NUM_CLASSES)},
        "boards": boards_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-boards", type=int, default=1200)
    parser.add_argument("--val-boards", type=int, default=200)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-boards", type=int, default=8, help="boards per step (x64 crops)")
    parser.add_argument("--lr", type=float, default=3e-3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=str, default="models/square-classifier.onnx")
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    generator = torch.Generator().manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}")

    print(f"rendering {args.train_boards} training boards...")
    started = time.perf_counter()
    train_crops, train_labels = build_cache(
        args.train_boards, seed=args.seed, workers=args.workers
    )
    # Validation uses a disjoint seed and no corner jitter: we want the model's
    # accuracy, not the augmentation's.
    val_crops, val_labels = build_cache(
        args.val_boards, seed=args.seed + 9999, jitter=0.0, workers=args.workers
    )
    print(f"rendered in {time.perf_counter() - started:.0f}s")

    crops_per_batch = args.batch_boards * 64
    train_x = to_tensor(train_crops)
    train_y = torch.from_numpy(train_labels)
    val_x = to_tensor(val_crops)
    val_y = torch.from_numpy(val_labels)
    steps_per_epoch = len(train_x) // crops_per_batch

    model = SquareNet().to(device)
    parameters = sum(p.numel() for p in model.parameters())
    print(f"parameters: {parameters:,}")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=args.lr, epochs=args.epochs, steps_per_epoch=steps_per_epoch
    )
    # Empty squares are ~70% of a board, so without smoothing the model is rewarded
    # for predicting "empty" confidently and learns to be overconfident -- which
    # matters here because confidence gates whether analysis is shown at all.
    criterion = nn.CrossEntropyLoss(label_smoothing=0.05)

    best_board_accuracy = -1.0
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        started = time.perf_counter()
        running = 0.0

        for index in batches(len(train_x), crops_per_batch, shuffle=True, generator=generator):
            batch = normalise(augment(train_x[index].to(device), generator))
            target = train_y[index].to(device)

            optimiser.zero_grad(set_to_none=True)
            loss = criterion(model(batch), target)
            loss.backward()
            optimiser.step()
            schedule.step()
            running += loss.detach().item()

        metrics = evaluate(model, val_x, val_y, device)
        elapsed = time.perf_counter() - started
        print(
            f"epoch {epoch:2d}  loss {running / max(steps_per_epoch, 1):.4f}"
            f"  square {metrics['square_accuracy'] * 100:6.2f}%"
            f"  board {metrics['board_accuracy'] * 100:6.2f}%"
            f"  ({elapsed:.0f}s)"
        )

        if metrics["board_accuracy"] > best_board_accuracy:
            best_board_accuracy = metrics["board_accuracy"]
            export_onnx(model.cpu(), str(output))
            model.to(device)

    print(f"\nbest board accuracy: {best_board_accuracy * 100:.2f}%")
    print(f"exported: {output}  ({output.stat().st_size / 1024:.0f} KB)")

    final = evaluate(model, val_x, val_y, device)
    print("\nper-class recall (validation):")
    for symbol, value in final["class_recall"].items():
        support = final["class_support"][symbol]
        if support:
            print(f"  {symbol!r:4}  {value * 100:6.2f}%   (n={support})")


if __name__ == "__main__":
    main()
