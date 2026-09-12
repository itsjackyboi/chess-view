"""The synthetic renderer.

Training data quality is invisible until the model underperforms and nobody knows
why, so the properties that matter -- correct labels, real variation, no rendering
corruption -- are asserted here rather than assumed.
"""

from __future__ import annotations

import chess
import numpy as np
import pytest

from chessview_vision.synth import (
    NUM_CLASSES,
    _apply_lighting,
    PIECE_CLASSES,
    labels_to_placement,
    placement_to_labels,
    random_placement,
    random_style,
    render_board,
)


class TestLabels:
    def test_starting_position_round_trips(self):
        placement = chess.Board().board_fen()
        assert labels_to_placement(placement_to_labels(placement)) == placement

    @pytest.mark.parametrize("seed", range(8))
    def test_arbitrary_positions_round_trip(self, seed: int):
        placement = random_placement(np.random.default_rng(seed))
        assert labels_to_placement(placement_to_labels(placement)) == placement

    def test_empty_board_round_trips(self):
        assert labels_to_placement(placement_to_labels("8/8/8/8/8/8/8/8")) == "8/8/8/8/8/8/8/8"

    def test_labels_are_in_fen_reading_order(self):
        # a8 is index 0 and h1 is index 63, matching board.square_crops.
        labels = placement_to_labels("r7/8/8/8/8/8/8/7R")
        assert PIECE_CLASSES[labels[0]] == "r"
        assert PIECE_CLASSES[labels[63]] == "R"

    def test_a_short_placement_is_rejected(self):
        # Silently accepting this would mislabel every square after the gap.
        with pytest.raises(ValueError, match="expected 64"):
            placement_to_labels("8/8/8")

    def test_class_list_covers_empty_plus_both_colours(self):
        assert NUM_CLASSES == 13
        assert PIECE_CLASSES[0] == "."


class TestRendering:
    def test_produces_an_image_with_matching_labels(self):
        rng = np.random.default_rng(0)
        placement = chess.Board().board_fen()
        rendered = render_board(placement, rng)

        assert rendered.image.dtype == np.uint8
        assert rendered.image.ndim == 3
        assert labels_to_placement(rendered.labels) == placement
        assert rendered.corners.shape == (4, 2)

    @pytest.mark.parametrize("seed", range(6))
    def test_lighting_never_wraps_bright_pixels_to_black(self, seed: int):
        """Regression: multiplying uint8 in place wrapped brightened pixels around.

        A pixel at 250 scaled by 1.1 became 19, which showed up as lurid diagonal
        bands and would have taught the model that a lit board contains impossible
        colours. Tested on a uniformly bright canvas, where any dark output pixel
        can only have come from overflow.
        """
        canvas = np.full((128, 128, 3), 250, dtype=np.uint8)
        _apply_lighting(canvas, np.random.default_rng(seed))
        assert canvas.min() > 150, "a bright canvas darkened -- overflow"

    def test_pieces_are_actually_drawn(self):
        rng = np.random.default_rng(1)
        empty = render_board("8/8/8/8/8/8/8/8", rng, perspective=False, style=random_style(rng))
        rng = np.random.default_rng(1)
        full = render_board(
            chess.Board().board_fen(), rng, perspective=False, style=random_style(rng)
        )
        assert full.image.std() > empty.image.std()

    def test_randomisation_produces_genuinely_different_images(self):
        # Identical renders would mean the model memorises one board.
        rng = np.random.default_rng(4)
        placement = chess.Board().board_fen()
        first = render_board(placement, rng).image
        second = render_board(placement, rng).image
        assert first.shape != second.shape or not np.array_equal(first, second)

    def test_perspective_returns_the_warped_corners(self):
        rng = np.random.default_rng(6)
        rendered = render_board(chess.Board().board_fen(), rng, perspective=True)
        # A perspective render is padded, so corners move off the origin.
        assert rendered.corners.min() > 0

    def test_without_perspective_the_board_fills_the_frame(self):
        rng = np.random.default_rng(6)
        rendered = render_board(chess.Board().board_fen(), rng, perspective=False)
        assert rendered.image.shape[0] == rendered.image.shape[1]


class TestPositionSampling:
    def test_sampled_positions_are_legal_boards(self):
        """Random legal play, not random scatter.

        Uniformly sprinkling pieces would train on boards that cannot occur and
        under-represent the empty squares that dominate a real position.
        """
        rng = np.random.default_rng(9)
        for _ in range(20):
            board = chess.Board(None)
            board.set_board_fen(random_placement(rng))
            # Exactly one king per side is the invariant random scatter would break.
            assert len(board.pieces(chess.KING, chess.WHITE)) == 1
            assert len(board.pieces(chess.KING, chess.BLACK)) == 1

    def test_most_squares_are_empty_as_in_a_real_game(self):
        rng = np.random.default_rng(10)
        empties = [
            (placement_to_labels(random_placement(rng)) == 0).sum() for _ in range(20)
        ]
        assert 28 <= float(np.mean(empties)) <= 56
