"""Board localisation and square extraction.

Synthetic renders are the fixtures: they come with ground-truth corners, so
detection can be scored against a known answer rather than eyeballed.
"""

from __future__ import annotations

import chess
import numpy as np
import pytest

from chessview_vision.board import (
    BOARD_PX,
    BoardNotFound,
    find_board,
    geometry_from_corners,
    homography_for,
    order_corners,
    orient,
    square_crops,
)
from chessview_vision.synth import render_board, random_placement

SQUARE = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)


class TestOrderCorners:
    def test_already_ordered_stays_put(self):
        assert np.allclose(order_corners(SQUARE), SQUARE)

    @pytest.mark.parametrize("shift", [1, 2, 3])
    def test_rotations_normalise_to_the_same_order(self, shift: int):
        """Corner order must not depend on which corner the caller listed first.

        An inconsistent order flips or rotates the homography, which mirrors the
        board and yields a plausible-looking but entirely wrong position.
        """
        rotated = np.roll(SQUARE, shift, axis=0)
        assert np.allclose(order_corners(rotated), SQUARE)

    def test_reversed_winding_normalises(self):
        assert np.allclose(order_corners(SQUARE[::-1]), SQUARE)

    def test_handles_a_perspective_skewed_quad(self):
        skewed = np.array([[20, 5], [90, 12], [105, 95], [5, 88]], dtype=np.float32)
        ordered = order_corners(skewed)
        # Top corners above bottom corners, left corners left of right corners.
        assert ordered[0][1] < ordered[3][1]
        assert ordered[0][0] < ordered[1][0]


class TestHomography:
    def test_maps_corners_onto_the_canonical_square(self):
        matrix = homography_for(SQUARE, size=BOARD_PX)
        points = np.array([SQUARE], dtype=np.float32)
        mapped = __import__("cv2").perspectiveTransform(points, matrix)[0]

        expected = np.array(
            [[0, 0], [BOARD_PX - 1, 0], [BOARD_PX - 1, BOARD_PX - 1], [0, BOARD_PX - 1]],
            dtype=np.float32,
        )
        assert np.allclose(mapped, expected, atol=1e-3)

    def test_warping_a_rendered_board_recovers_a_square(self):
        rng = np.random.default_rng(3)
        rendered = render_board(chess.Board().board_fen(), rng)
        geometry = geometry_from_corners(rendered.corners)
        warped = geometry.warp(rendered.image)
        assert warped.shape[:2] == (BOARD_PX, BOARD_PX)


class TestDetection:
    def test_finds_most_synthetic_boards(self):
        """Detection is a convenience, not the primary path.

        In normal operation the client supplies corners from calibration, so this
        only has to work well enough to offer the user a starting guess.
        """
        rng = np.random.default_rng(11)
        found = 0
        for _ in range(25):
            rendered = render_board(random_placement(rng), rng)
            try:
                find_board(rendered.image)
                found += 1
            except BoardNotFound:
                pass
        assert found >= 20, f"only located {found}/25 boards"

    def test_detected_corners_land_within_about_a_square(self):
        """Detection is assistive, and its accuracy reflects that.

        Measured median error is ~0.6 squares, which is too loose to classify
        against directly -- misaligned crops cut pieces in half. The exact corners
        come from the client's calibration instead, and detection only has to be
        close enough to offer the user a starting guess to adjust.

        Asserted on the median over many boards rather than one sample, since the
        contour fallback occasionally latches onto a board edge and does worse.
        """
        rng = np.random.default_rng(5)
        errors = []
        for _ in range(30):
            rendered = render_board(random_placement(rng), rng)
            try:
                geometry = find_board(rendered.image)
            except BoardNotFound:
                continue
            truth = order_corners(rendered.corners)
            square_px = rendered.image.shape[0] / 8
            errors.append(np.linalg.norm(geometry.corners - truth, axis=1).max() / square_px)

        assert len(errors) >= 20, "too few detections to judge accuracy"
        assert float(np.median(errors)) < 1.0

    def test_reports_no_board_rather_than_guessing(self):
        # A plain gradient contains no board; inventing corners here would send the
        # classifier a meaningless crop and produce a confident wrong position.
        noise = np.tile(np.linspace(0, 255, 400, dtype=np.uint8), (400, 1))
        with pytest.raises(BoardNotFound):
            find_board(noise)

    def test_records_which_method_succeeded(self):
        rng = np.random.default_rng(2)
        rendered = render_board(chess.Board().board_fen(), rng)
        assert find_board(rendered.image).source in {"pattern", "contour"}


class TestSquareCrops:
    def test_returns_sixty_four_crops_in_fen_order(self):
        board = np.zeros((BOARD_PX, BOARD_PX, 3), dtype=np.uint8)
        # Mark a8 (index 0) so ordering can be checked.
        board[0:64, 0:64] = 255
        crops = square_crops(board)

        assert crops.shape == (64, 64, 64, 3)
        assert crops[0].mean() > 200, "first crop should be a8"
        assert crops[63].mean() < 50, "last crop should be h1"

    def test_crops_extend_upward_for_tall_pieces(self):
        """A crop limited to its own square would cut a piece off at the collar.

        Here a marker sits above the square's own bounds; the crop must still see it.
        """
        board = np.zeros((BOARD_PX, BOARD_PX, 3), dtype=np.uint8)
        square = BOARD_PX // 8
        # Paint just above the d5 square (row 3, col 3).
        board[3 * square - 20 : 3 * square, 3 * square : 4 * square] = 255

        crops = square_crops(board, rise=1.0)
        assert crops[3 * 8 + 3].max() == 255

    def test_handles_greyscale_input(self):
        crops = square_crops(np.zeros((BOARD_PX, BOARD_PX), dtype=np.uint8))
        assert crops.shape == (64, 64, 64)

    def test_back_rank_crops_do_not_fall_outside_the_image(self):
        # Squares on the top row have nothing above them to extend into.
        crops = square_crops(np.full((BOARD_PX, BOARD_PX, 3), 128, dtype=np.uint8))
        assert crops[:8].shape == (8, 64, 64, 3)
        assert not np.isnan(crops).any()


class TestOrientation:
    def test_white_at_bottom_is_left_alone(self):
        image = np.arange(BOARD_PX * BOARD_PX, dtype=np.uint8).reshape(BOARD_PX, BOARD_PX)
        assert np.array_equal(orient(image, white_at_bottom=True), image)

    def test_black_at_bottom_is_rotated_so_a8_is_top_left(self):
        image = np.zeros((BOARD_PX, BOARD_PX), dtype=np.uint8)
        image[0:10, 0:10] = 255
        rotated = orient(image, white_at_bottom=False)
        assert rotated[-10:, -10:].mean() > 200
