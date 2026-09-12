"""The real detector: a rectified board image in, piece placement out.

Implements the `Detector` protocol from detector.py, so it drops into the pipeline
the stub detector proved at M0 with no other changes.

**Where rectification happens.** The architecture has the client warp the board to a
flat square before sending, which is both the bandwidth saving and the privacy
property -- the room never leaves the device. That lands at M3. Until the client does
it, this falls back to localising the board server-side, which works but sends whole
frames and is meaningfully less accurate: detected corners land about 0.6 squares
from the truth, and a misaligned crop cuts pieces in half.

`expect_rectified` controls which path runs, so the server is ready before the client
catches up.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from chessview_vision.board import (
    BOARD_PX,
    BoardGeometry,
    BoardNotFound,
    find_board,
    geometry_from_corners,
    orient,
    square_crops,
)
from chessview_vision.classifier import INPUT_PX, SquareClassifier, SquarePredictions
from chessview_vision.detector import Detection

log = logging.getLogger(__name__)


class CnnDetector:
    """Classifies a board image using the trained per-square model."""

    def __init__(
        self,
        classifier: SquareClassifier,
        *,
        expect_rectified: bool = True,
        white_at_bottom: bool = True,
    ) -> None:
        self._classifier = classifier
        self._expect_rectified = expect_rectified
        self._white_at_bottom = white_at_bottom
        # Reused across frames while the camera holds still: re-localising every
        # frame would be wasted work and would make the crop jitter frame to frame
        # even when nothing on the board moved.
        self._geometry: BoardGeometry | None = None

    def set_corners(self, corners: np.ndarray) -> None:
        """Adopt geometry from the client's calibration.

        Preferred over detection: these corners come from the user placing them, so
        they are exact, where detection is only close.
        """
        self._geometry = geometry_from_corners(np.asarray(corners, dtype=np.float32))

    def forget_geometry(self) -> None:
        self._geometry = None

    async def detect(self, jpeg: bytes) -> Detection | None:
        image = _decode(jpeg)
        if image is None:
            return None

        board = self._rectify(image)
        if board is None:
            return None

        board = orient(board, white_at_bottom=self._white_at_bottom)
        crops = square_crops(board, crop_px=INPUT_PX)
        predictions = self._classifier.predict(crops)

        return _to_detection(predictions)

    def _rectify(self, image: np.ndarray) -> np.ndarray | None:
        if self._expect_rectified:
            # The client already warped it; just bring it to the canonical size.
            if image.shape[0] != image.shape[1]:
                log.warning("expected a square rectified board, got %s", image.shape)
                return None
            if image.shape[0] != BOARD_PX:
                return cv2.resize(image, (BOARD_PX, BOARD_PX), interpolation=cv2.INTER_AREA)
            return image

        if self._geometry is None:
            try:
                self._geometry = find_board(image)
            except BoardNotFound:
                # Distinct from a low-confidence reading: the client shows "point the
                # camera at the board" rather than "I can't make this out".
                return None

        return self._geometry.warp(image)


def _decode(jpeg: bytes) -> np.ndarray | None:
    buffer = np.frombuffer(jpeg, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        log.warning("could not decode a %d-byte frame", len(jpeg))
    return image


def _to_detection(predictions: SquarePredictions) -> Detection:
    """Package predictions, reporting the *weakest* square as the board's confidence.

    Not the mean. A board is only as trustworthy as its worst square -- see the
    compounding table in docs/architecture.md -- and averaging hides precisely the
    one square that is wrong, which is the square that makes the whole position
    wrong.
    """
    return Detection(
        placement=predictions.placement,
        confidence=predictions.min_confidence,
        squares={index: float(value) for index, value in enumerate(predictions.confidence)},
    )
