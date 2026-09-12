"""Board localisation: find the board in an image and flatten it.

This is stage one of the two-stage vision design in docs/architecture.md. Getting a
rectified, axis-aligned board here is what lets stage two be a small per-square
classifier instead of a general object detector: after warping, every square is at a
known fixed location, so the model never has to learn perspective invariance.

Three ways of finding the corners, tried in order of reliability:

1. Corners supplied by the client. The app has the user align the board once, and
   tracks it with optical flow afterwards, so in normal operation the geometry
   arrives with the frame and none of the detection below runs.
2. OpenCV's chessboard-pattern detector, which is purpose-built for this and very
   accurate -- when it works. It needs to see the checkerboard pattern, so a board
   with pieces on it frequently defeats it.
3. A contour-based search for the largest four-sided shape, which survives occlusion
   far better but is looser about exactly where the edge falls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Side length of the rectified board in pixels: 64px per square. Enough detail to
# tell a bishop from a pawn, small enough to keep inference and uplink cheap.
BOARD_PX = 512
SQUARE_PX = BOARD_PX // 8

# Pieces are tall: a knight on one square occupies a good deal of the square behind
# it in the image. Each crop therefore extends upward by this fraction of a square,
# so the classifier sees the whole piece rather than its base.
CROP_RISE = 1.0


class BoardNotFound(RuntimeError):
    """No board could be located in the image."""


@dataclass(slots=True, frozen=True)
class BoardGeometry:
    """Where the board is, and how to flatten it."""

    # Four corners in image coordinates, ordered top-left, top-right, bottom-right,
    # bottom-left of the board as it appears.
    corners: np.ndarray
    homography: np.ndarray
    # How the detector found it, for diagnostics and for the client's confidence UI.
    source: str

    def warp(self, image: np.ndarray, size: int = BOARD_PX) -> np.ndarray:
        return cv2.warpPerspective(image, self.homography, (size, size))


def order_corners(points: np.ndarray) -> np.ndarray:
    """Put four points in a consistent top-left, top-right, bottom-right, bottom-left.

    Without a fixed order the homography can flip or rotate the board, which would
    silently mirror the position -- the kind of bug that produces a plausible-looking
    but completely wrong FEN.

    Uses coordinate sums and differences rather than angles: the top-left corner has
    the smallest x+y and the top-right the smallest y-x, which holds for any
    convex quadrilateral regardless of rotation up to 45 degrees.
    """
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)

    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]  # top-left
    ordered[2] = points[np.argmax(total)]  # bottom-right

    diff = np.diff(points, axis=1).ravel()  # y - x
    ordered[1] = points[np.argmin(diff)]  # top-right
    ordered[3] = points[np.argmax(diff)]  # bottom-left
    return ordered


def homography_for(corners: np.ndarray, size: int = BOARD_PX) -> np.ndarray:
    """Map the four board corners onto a square of `size` pixels."""
    target = np.array(
        [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(order_corners(corners), target)
    return matrix.astype(np.float32)


def geometry_from_corners(corners: np.ndarray, *, source: str = "client") -> BoardGeometry:
    ordered = order_corners(corners)
    return BoardGeometry(ordered, homography_for(ordered), source)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def find_board(image: np.ndarray) -> BoardGeometry:
    """Locate the board, trying the accurate method before the robust one."""
    grey = _to_grey(image)

    corners = _find_by_pattern(grey)
    if corners is not None:
        return geometry_from_corners(corners, source="pattern")

    corners = _find_by_contour(grey)
    if corners is not None:
        return geometry_from_corners(corners, source="contour")

    raise BoardNotFound("no board-like quadrilateral in the image")


def _to_grey(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _find_by_pattern(grey: np.ndarray) -> np.ndarray | None:
    """Use OpenCV's chessboard detector on the 7x7 grid of interior corners.

    Accurate to sub-pixel when it succeeds, but it needs an unobstructed view of the
    checkerboard, so on a board mid-game it usually does not. That is expected --
    this is the fast path, not the dependable one.
    """
    flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE
    try:
        found, corners = cv2.findChessboardCornersSB(grey, (7, 7), flags=flags)
    except cv2.error:
        return None
    if not found or corners is None:
        return None

    # The detector returns interior corners only, so the outer edge of the board is
    # half a square beyond the outermost ones. Extrapolating along the grid's own
    # axes keeps the estimate correct under perspective.
    grid = corners.reshape(7, 7, 2)
    return _extrapolate_outer(grid)


def _extrapolate_outer(grid: np.ndarray) -> np.ndarray:
    """Extend a 7x7 interior-corner grid to the board's four outer corners."""
    top_left, top_right = grid[0, 0], grid[0, -1]
    bottom_left, bottom_right = grid[-1, 0], grid[-1, -1]

    # One grid step is 1/6 of the span between outermost interior corners; the board
    # edge lies half a square further out, hence the 1/12 factors below.
    def extend(corner: np.ndarray, along: np.ndarray, down: np.ndarray) -> np.ndarray:
        return corner + along / 12.0 + down / 12.0

    horizontal = top_right - top_left
    vertical = bottom_left - top_left

    return np.array(
        [
            extend(top_left, -horizontal, -vertical),
            extend(top_right, horizontal, -vertical),
            extend(bottom_right, horizontal, vertical),
            extend(bottom_left, -horizontal, vertical),
        ],
        dtype=np.float32,
    )


def _find_by_contour(grey: np.ndarray, *, min_area_fraction: float = 0.08) -> np.ndarray | None:
    """Find the largest convincing quadrilateral.

    Survives pieces covering the pattern, which is the common case in a real game.
    Adaptive thresholding rather than a global one, because board lighting is
    routinely uneven -- a window on one side is enough to defeat a single threshold.
    """
    blurred = cv2.GaussianBlur(grey, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5
    )
    edges = cv2.Canny(binary, 50, 150)
    # Close gaps where a piece interrupts a board edge, so the outline stays one
    # contour instead of fragmenting.
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    image_area = float(grey.shape[0] * grey.shape[1])
    best: np.ndarray | None = None
    best_area = min_area_fraction * image_area

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < best_area:
            continue
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if not _is_roughly_square(approx):
            continue
        best, best_area = approx.reshape(4, 2).astype(np.float32), area

    return best


def _is_roughly_square(quad: np.ndarray, *, tolerance: float = 2.2) -> bool:
    """Reject quadrilaterals too elongated to be a board seen from a sane angle.

    A chess board is square; perspective can skew it, but an aspect ratio beyond
    this means we have latched onto a table edge or a door frame.
    """
    points = quad.reshape(4, 2).astype(np.float32)
    ordered = order_corners(points)
    widths = [
        np.linalg.norm(ordered[1] - ordered[0]),
        np.linalg.norm(ordered[2] - ordered[3]),
    ]
    heights = [
        np.linalg.norm(ordered[3] - ordered[0]),
        np.linalg.norm(ordered[2] - ordered[1]),
    ]
    width, height = max(widths), max(heights)
    if width <= 0 or height <= 0:
        return False
    ratio = max(width / height, height / width)
    return ratio <= tolerance


# --------------------------------------------------------------------------
# Square extraction
# --------------------------------------------------------------------------


def square_crops(
    rectified: np.ndarray,
    *,
    crop_px: int = 64,
    rise: float = CROP_RISE,
) -> np.ndarray:
    """Cut a rectified board into 64 crops, in FEN order (a8 first, h1 last).

    Each crop extends upward beyond its own square by `rise` squares. Pieces are
    tall and a rectified board is still a projection, so a piece's head sits well
    above its base -- a crop limited to the square itself would show the classifier
    a pawn's collar and a queen's collar and little else to tell them apart.

    Returns an array of shape (64, crop_px, crop_px, channels).
    """
    size = rectified.shape[0]
    square = size / 8.0
    rise_px = int(round(square * rise))

    channels = () if rectified.ndim == 2 else (rectified.shape[2],)
    crops = np.zeros((64, crop_px, crop_px, *channels), dtype=rectified.dtype)

    # Pad the top so squares on the back rank can still extend upward without
    # falling outside the image.
    pad_spec = ((rise_px, 0), (0, 0)) + ((0, 0),) * len(channels)
    padded = np.pad(rectified, pad_spec, mode="edge")

    for index in range(64):
        row, col = divmod(index, 8)
        top = int(round(row * square))  # already offset by the pad
        bottom = int(round((row + 1) * square)) + rise_px
        left = int(round(col * square))
        right = int(round((col + 1) * square))

        patch = padded[top:bottom, left:right]
        crops[index] = cv2.resize(patch, (crop_px, crop_px), interpolation=cv2.INTER_AREA)

    return crops


def orient(rectified: np.ndarray, *, white_at_bottom: bool) -> np.ndarray:
    """Rotate so a8 is top-left, which is the order FEN and square_crops assume.

    When the camera sits behind black, the board arrives upside down; rotating it
    here means everything downstream works in one orientation only.
    """
    if white_at_bottom:
        return rectified
    return cv2.rotate(rectified, cv2.ROTATE_180)
