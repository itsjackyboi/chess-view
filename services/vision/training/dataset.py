"""Training data: rendered boards turned into labelled square crops.

Generated on the fly rather than written to disk. Synthetic data is cheap to make
and its whole value is variety, so a fixed dump would just be a smaller, staler
version of what this produces -- and 100k crops on disk is half a gigabyte for no
benefit.

Each index deterministically seeds its own board, so the dataset is reproducible,
shuffles correctly, and works across DataLoader workers without them colliding.
"""

from __future__ import annotations

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from chessview_vision.board import BOARD_PX, homography_for, square_crops
from chessview_vision.classifier import INPUT_PX
from chessview_vision.synth import random_placement, render_board

# How far, as a fraction of one square, the corners may be off when training.
# Calibration is never pixel-exact and optical-flow tracking drifts, so a model
# trained on perfect alignment falls apart in the field. Deliberately wider than the
# error we expect, to leave headroom.
CORNER_JITTER = 0.18


class SquareCropDataset(Dataset):
    """Labelled 48x48 square crops.

    Args:
        boards: how many distinct boards this dataset represents.
        seed:   base seed; combined with the index so every board is reproducible.
        jitter: corner noise as a fraction of a square. Set 0 for validation, to
                measure the model rather than the augmentation.
    """

    def __init__(self, boards: int, *, seed: int = 0, jitter: float = CORNER_JITTER) -> None:
        self.boards = boards
        self.seed = seed
        self.jitter = jitter

    def __len__(self) -> int:
        return self.boards

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        rng = np.random.default_rng((self.seed, index))

        rendered = render_board(random_placement(rng), rng)
        corners = self._perturb(rendered.corners, rng)

        warped = cv2.warpPerspective(
            rendered.image, homography_for(corners), (BOARD_PX, BOARD_PX)
        )
        crops = square_crops(warped, crop_px=INPUT_PX)

        # Standardised per crop, matching classifier.preprocess exactly. Any drift
        # between the two would show as a model that scores well offline and badly
        # in the service.
        batch = crops.astype(np.float32) / 255.0
        mean = batch.mean(axis=(1, 2, 3), keepdims=True)
        std = batch.std(axis=(1, 2, 3), keepdims=True)
        batch = (batch - mean) / np.maximum(std, 1e-5)

        return (
            torch.from_numpy(np.ascontiguousarray(batch.transpose(0, 3, 1, 2))),
            torch.from_numpy(rendered.labels),
        )

    def _perturb(self, corners: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if self.jitter <= 0:
            return corners
        # One square in the source image, approximated from the corner span.
        span = float(np.linalg.norm(corners[1] - corners[0])) / 8.0
        noise = rng.uniform(-self.jitter, self.jitter, corners.shape) * span
        return (corners + noise).astype(np.float32)


def collate(batch: list[tuple[torch.Tensor, torch.Tensor]]) -> tuple[torch.Tensor, torch.Tensor]:
    """Flatten boards into one crop batch.

    The network classifies squares, not boards, so a batch of B boards is really
    64*B independent examples.
    """
    crops = torch.cat([item[0] for item in batch], dim=0)
    labels = torch.cat([item[1] for item in batch], dim=0)
    return crops, labels


# --------------------------------------------------------------------------
# Cached variant
# --------------------------------------------------------------------------


def _render_one(task: tuple[int, int, float]) -> tuple[np.ndarray, np.ndarray]:
    """Render and crop a single board. Module-level so it can be pickled to workers."""
    seed, index, jitter = task
    dataset = SquareCropDataset(1, seed=seed, jitter=jitter)
    rng = np.random.default_rng((seed, index))

    rendered = render_board(random_placement(rng), rng)
    corners = dataset._perturb(rendered.corners, rng)  # noqa: SLF001 - same module
    warped = cv2.warpPerspective(
        rendered.image, homography_for(corners), (BOARD_PX, BOARD_PX)
    )
    return square_crops(warped, crop_px=INPUT_PX), rendered.labels


def build_cache(
    boards: int, *, seed: int = 0, jitter: float = CORNER_JITTER, workers: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    """Render a fixed set of boards once, as uint8 crops.

    Rendering dominates training time -- roughly 230 ms per board against a few
    milliseconds for the forward and backward pass over its 64 crops. Regenerating
    every epoch means spending 98% of the run in OpenCV. Caching once and applying
    cheap per-epoch augmentation instead gets the same regularisation for a fraction
    of the cost.

    Returns crops of shape (boards*64, 48, 48, 3) and labels of shape (boards*64,).
    """
    from multiprocessing import Pool

    tasks = [(seed, index, jitter) for index in range(boards)]
    crops = np.empty((boards * 64, INPUT_PX, INPUT_PX, 3), dtype=np.uint8)
    labels = np.empty(boards * 64, dtype=np.int64)

    with Pool(processes=max(1, workers)) as pool:
        for index, (board_crops, board_labels) in enumerate(
            pool.imap(_render_one, tasks, chunksize=8)
        ):
            start = index * 64
            crops[start:start + 64] = board_crops
            labels[start:start + 64] = board_labels

    return crops, labels


class CachedSquareDataset(Dataset):
    """Pre-rendered crops with cheap photometric augmentation.

    The augmentation here is intentionally limited to brightness, contrast and
    noise. Geometry is baked into the cache, so the expensive variation -- position,
    perspective, corner jitter -- comes from having many distinct boards rather than
    from re-warping the same ones.
    """

    def __init__(
        self,
        crops: np.ndarray,
        labels: np.ndarray,
        *,
        augment: bool = True,
        seed: int = 0,
    ) -> None:
        self.crops = crops
        self.labels = labels
        self.augment = augment
        self.seed = seed

    def __len__(self) -> int:
        return len(self.crops)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        crop = self.crops[index].astype(np.float32)

        if self.augment:
            rng = np.random.default_rng((self.seed, index, int(torch.randint(0, 1 << 30, (1,)))))
            crop = crop * rng.uniform(0.75, 1.25) + rng.uniform(-25, 25)
            crop += rng.normal(0, rng.uniform(0, 7), crop.shape)
            np.clip(crop, 0, 255, out=crop)

        crop /= 255.0
        crop = (crop - crop.mean()) / max(float(crop.std()), 1e-5)

        return (
            torch.from_numpy(np.ascontiguousarray(crop.transpose(2, 0, 1))),
            torch.tensor(self.labels[index]),
        )
