"""Per-square piece classification.

Stage two of the vision design: given a rectified board, decide what stands on each
of the 64 squares. Because stage one removed the perspective, every crop arrives
axis-aligned and consistently scaled, so this can be a small CNN over fixed crops
rather than a general object detector over a skewed scene.

Inference runs on **onnxruntime only**. PyTorch is a training-time dependency and is
not installed in the deployed service -- it would add well over a gigabyte to an
image whose entire job is a ~500 KB model.

All 64 squares go through as a single batch. One board is one inference call, which
is what keeps this viable on CPU and is the reason the hosting recommendation in
docs/architecture.md does not call for a GPU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from chessview_vision.synth import NUM_CLASSES, PIECE_CLASSES, labels_to_placement

log = logging.getLogger(__name__)

# Crop size fed to the network. Small enough that 64 of them are cheap, large enough
# to tell a bishop's mitre from a pawn's head.
INPUT_PX = 48


class ModelUnavailable(RuntimeError):
    """No usable model file. Surfaced rather than silently degrading."""


@dataclass(slots=True)
class SquarePredictions:
    """Raw per-square output, before any chess reasoning is applied."""

    labels: np.ndarray      # (64,) argmax class index
    confidence: np.ndarray  # (64,) probability of the chosen class

    @property
    def placement(self) -> str:
        return labels_to_placement(self.labels)

    @property
    def min_confidence(self) -> float:
        """The weakest square on the board.

        Reported rather than the mean deliberately: a board is only as trustworthy
        as its worst square, and averaging hides exactly the one square that is
        wrong. See docs/architecture.md on how per-square error compounds.
        """
        return float(self.confidence.min())

    def weakest_squares(self, count: int = 3) -> list[tuple[int, float]]:
        """The least certain squares, for the UI to point the user at."""
        order = np.argsort(self.confidence)[:count]
        return [(int(i), float(self.confidence[i])) for i in order]


def preprocess(crops: np.ndarray) -> np.ndarray:
    """Crops to a normalised NCHW float batch.

    Per-image standardisation rather than a fixed dataset mean: board and piece
    colours vary enormously between chess sets and lighting conditions, and
    normalising each crop against itself removes most of that variation before the
    network ever sees it.
    """
    import cv2  # local import: keeps module import cheap for callers that only need types

    batch = np.empty((len(crops), INPUT_PX, INPUT_PX, 3), dtype=np.float32)
    for index, crop in enumerate(crops):
        resized = cv2.resize(crop, (INPUT_PX, INPUT_PX), interpolation=cv2.INTER_AREA)
        if resized.ndim == 2:
            resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
        batch[index] = resized

    batch /= 255.0
    mean = batch.mean(axis=(1, 2, 3), keepdims=True)
    std = batch.std(axis=(1, 2, 3), keepdims=True)
    batch = (batch - mean) / np.maximum(std, 1e-5)

    return np.ascontiguousarray(batch.transpose(0, 3, 1, 2))


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=-1, keepdims=True)


class SquareClassifier:
    """ONNX inference over the 64 square crops of one board."""

    def __init__(self, model_path: str | Path, *, threads: int = 2) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise ModelUnavailable(f"no model at {path}")

        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - deployment misconfiguration
            raise ModelUnavailable("onnxruntime is not installed") from exc

        options = ort.SessionOptions()
        # Pinned rather than left to grow: the engine pool on the same host needs
        # its cores, and an inference library helping itself to all of them would
        # slow down analysis to speed up detection.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self.model_path = path
        log.info("loaded square classifier from %s", path)

    def predict(self, crops: np.ndarray) -> SquarePredictions:
        if len(crops) != 64:
            raise ValueError(f"expected 64 crops, got {len(crops)}")

        logits = self._session.run(None, {self._input_name: preprocess(crops)})[0]
        probabilities = softmax(logits)
        labels = probabilities.argmax(axis=1)

        return SquarePredictions(
            labels=labels.astype(np.int64),
            confidence=probabilities[np.arange(64), labels].astype(np.float32),
        )


def class_name(index: int) -> str:
    return PIECE_CLASSES[index]


__all__ = [
    "INPUT_PX",
    "NUM_CLASSES",
    "ModelUnavailable",
    "SquareClassifier",
    "SquarePredictions",
    "class_name",
    "preprocess",
    "softmax",
]
