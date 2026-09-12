"""The trained detector's plumbing.

Accuracy is measured by services/vision/evaluate.py against a held-out set; these
tests cover the paths around the model -- decoding, rectification, orientation, and
how uncertainty is reported -- which are what break silently.
"""

from __future__ import annotations

import os
from pathlib import Path

import chess
import cv2
import numpy as np
import pytest

from chessview_vision.board import BOARD_PX, homography_for
from chessview_vision.classifier import ModelUnavailable, SquareClassifier, softmax
from chessview_vision.cnn_detector import CnnDetector, _to_detection
from chessview_vision.classifier import SquarePredictions
from chessview_vision.synth import render_board

MODEL = Path(os.environ.get("CHESSVIEW_MODEL", "models/square-classifier.onnx"))
requires_model = pytest.mark.skipif(
    not MODEL.is_file(), reason=f"no classifier at {MODEL} (run training/train.py)"
)


def _jpeg(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return buffer.tobytes()


# --------------------------------------------------------------------------
# Confidence reporting -- no model needed
# --------------------------------------------------------------------------


class TestConfidenceReporting:
    def test_board_confidence_is_the_weakest_square_not_the_mean(self):
        """A board is only as trustworthy as its worst square.

        Averaging hides precisely the one square that is wrong, which is the square
        that makes the whole position wrong.
        """
        confidence = np.full(64, 0.99, dtype=np.float32)
        confidence[42] = 0.31
        predictions = SquarePredictions(np.zeros(64, dtype=np.int64), confidence)

        assert predictions.min_confidence == pytest.approx(0.31)
        detection = _to_detection(predictions)
        assert detection.confidence == pytest.approx(0.31)

    def test_weakest_squares_are_reported_worst_first(self):
        confidence = np.full(64, 0.99, dtype=np.float32)
        confidence[10] = 0.4
        confidence[20] = 0.2
        predictions = SquarePredictions(np.zeros(64, dtype=np.int64), confidence)

        assert [index for index, _ in predictions.weakest_squares(2)] == [20, 10]

    def test_per_square_confidence_reaches_the_client(self):
        predictions = SquarePredictions(
            np.zeros(64, dtype=np.int64), np.full(64, 0.8, dtype=np.float32)
        )
        assert len(_to_detection(predictions).squares) == 64


class TestSoftmax:
    def test_rows_sum_to_one(self):
        assert np.allclose(softmax(np.random.randn(5, 13)).sum(axis=1), 1.0)

    def test_is_stable_for_large_logits(self):
        # Without the max-subtraction this overflows to nan and every square
        # becomes "unknown" at once.
        result = softmax(np.array([[1000.0, 1001.0, 999.0]]))
        assert np.isfinite(result).all()
        assert result.argmax() == 1


class TestModelLoading:
    def test_a_missing_model_is_refused_loudly(self):
        # Silently degrading here would mean shipping a service that reports
        # scripted positions as if they were detected.
        with pytest.raises(ModelUnavailable, match="no model at"):
            SquareClassifier("/nonexistent/model.onnx")


# --------------------------------------------------------------------------
# Detection paths -- need a trained model
# --------------------------------------------------------------------------


@pytest.fixture
def classifier() -> SquareClassifier:
    return SquareClassifier(MODEL)


@requires_model
class TestDetection:
    async def test_reads_a_rectified_board(self, classifier: SquareClassifier):
        rng = np.random.default_rng(1)
        rendered = render_board(chess.Board().board_fen(), rng, perspective=False)
        rectified = cv2.warpPerspective(
            rendered.image, homography_for(rendered.corners), (BOARD_PX, BOARD_PX)
        )

        detector = CnnDetector(classifier, expect_rectified=True)
        detection = await detector.detect(_jpeg(rectified))

        assert detection is not None
        assert len(detection.placement.split("/")) == 8
        assert 0.0 <= detection.confidence <= 1.0

    async def test_rejects_a_non_square_image_when_expecting_a_rectified_board(
        self, classifier: SquareClassifier
    ):
        # A client that stopped warping would otherwise have its frames silently
        # stretched into nonsense.
        detector = CnnDetector(classifier, expect_rectified=True)
        wide = np.zeros((100, 400, 3), dtype=np.uint8)
        assert await detector.detect(_jpeg(wide)) is None

    async def test_localises_the_board_itself_when_asked_to(
        self, classifier: SquareClassifier
    ):
        rng = np.random.default_rng(2)
        rendered = render_board(chess.Board().board_fen(), rng)

        detector = CnnDetector(classifier, expect_rectified=False)
        detection = await detector.detect(_jpeg(rendered.image))
        assert detection is not None

    async def test_client_corners_take_precedence_over_detection(
        self, classifier: SquareClassifier
    ):
        """Calibration corners are exact; detection is only close.

        Setting them must stop the detector searching for its own.
        """
        rng = np.random.default_rng(3)
        rendered = render_board(chess.Board().board_fen(), rng)

        detector = CnnDetector(classifier, expect_rectified=False)
        detector.set_corners(rendered.corners)
        assert await detector.detect(_jpeg(rendered.image)) is not None

    async def test_undecodable_bytes_report_no_board_rather_than_raising(
        self, classifier: SquareClassifier
    ):
        detector = CnnDetector(classifier, expect_rectified=True)
        assert await detector.detect(b"this is not a jpeg") is None

    async def test_reads_the_starting_position_correctly(
        self, classifier: SquareClassifier
    ):
        """An end-to-end sanity check on the one position everyone can verify.

        Kept separate from the accuracy harness: if this fails, something is wired
        wrong -- orientation, crop order, class mapping -- rather than the model
        merely being imperfect.
        """
        rng = np.random.default_rng(4)
        rendered = render_board(chess.Board().board_fen(), rng, perspective=False)
        rectified = cv2.warpPerspective(
            rendered.image, homography_for(rendered.corners), (BOARD_PX, BOARD_PX)
        )

        detector = CnnDetector(classifier, expect_rectified=True)
        detection = await detector.detect(_jpeg(rectified))
        assert detection is not None

        truth = chess.Board().board_fen()
        matching = sum(
            1 for a, b in zip(_expand(detection.placement), _expand(truth)) if a == b
        )
        assert matching >= 58, f"only {matching}/64 squares matched on the start position"


def _expand(placement: str) -> list[str]:
    squares: list[str] = []
    for char in placement:
        if char == "/":
            continue
        if char.isdigit():
            squares.extend("." * int(char))
        else:
            squares.append(char)
    return squares


@requires_model
class TestConfidenceGate:
    async def test_a_misaligned_board_is_caught_by_low_confidence(
        self, classifier: SquareClassifier
    ):
        """The safety net that makes an imperfect detector survivable.

        A board rectified from corners half a square out produces crops with pieces
        cut in half. The classifier will still name something for every square, so
        the only thing standing between that and a confidently wrong position on
        screen is the confidence gate.
        """
        rng = np.random.default_rng(8)
        rendered = render_board(chess.Board().board_fen(), rng, perspective=False)

        square = rendered.image.shape[0] / 8
        shifted = rendered.corners + np.array([square * 0.5, square * 0.5], dtype=np.float32)
        skewed = cv2.warpPerspective(
            rendered.image, homography_for(shifted), (BOARD_PX, BOARD_PX)
        )

        detector = CnnDetector(classifier, expect_rectified=True)
        detection = await detector.detect(_jpeg(skewed))

        assert detection is not None
        assert detection.confidence < 0.6, (
            "a misaligned board must not be reported confidently -- the tracker's "
            "min_confidence gate is what stops it reaching the user as analysis"
        )
