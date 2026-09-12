"""Synthetic chess board renderer.

Generates labelled board images so the classifier has training data without a photo
collection effort standing between us and a working pipeline.

**What this does and does not buy us.** Synthetic data proves the pipeline works end
to end and gives a real, measurable accuracy number on data the model has not seen.
It does *not* predict accuracy on photographs of real boards: rendered pieces are
cleaner, better lit and more consistent than wood on a kitchen table, and a model
trained only on these will do markedly worse on real images. Closing that gap needs
real photographs, which is the M2 dataset work in docs/roadmap.md.

Randomisation is therefore deliberately aggressive -- board colours, piece palettes,
lighting gradients, blur, noise, perspective. Every axis of variation here is one
the model cannot memorise, which is what makes the accuracy figure mean something
and what gives real photographs a chance of falling inside the training distribution.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess
import cv2
import numpy as np

from chessview_vision.board import BOARD_PX

# Class ordering shared with the classifier: empty first, then white pieces, then
# black. Index 0 being "empty" is convenient -- it is the majority class.
PIECE_CLASSES: tuple[str, ...] = (
    ".",
    "P", "N", "B", "R", "Q", "K",
    "p", "n", "b", "r", "q", "k",
)
CLASS_INDEX = {symbol: index for index, symbol in enumerate(PIECE_CLASSES)}
NUM_CLASSES = len(PIECE_CLASSES)


@dataclass(slots=True)
class RenderedBoard:
    image: np.ndarray
    placement: str
    corners: np.ndarray
    labels: np.ndarray  # (64,) class indices in FEN order, a8 first


@dataclass(slots=True, frozen=True)
class RenderStyle:
    light_square: tuple[int, int, int]
    dark_square: tuple[int, int, int]
    white_piece: tuple[int, int, int]
    black_piece: tuple[int, int, int]
    outline: tuple[int, int, int]
    piece_scale: float


def random_style(rng: np.random.Generator) -> RenderStyle:
    """A plausible board and piece palette.

    Ranges are chosen so light and dark squares stay distinguishable and pieces stay
    distinguishable from the squares they sit on -- an unreadable board would just
    teach the model noise.
    """
    light = rng.integers(170, 245)
    dark = rng.integers(60, 140)
    tint = lambda base, spread: tuple(  # noqa: E731 - local shorthand
        int(np.clip(base + rng.integers(-spread, spread + 1), 0, 255)) for _ in range(3)
    )
    return RenderStyle(
        light_square=tint(light, 18),
        dark_square=tint(dark, 18),
        white_piece=tint(int(rng.integers(215, 252)), 8),
        black_piece=tint(int(rng.integers(18, 62)), 8),
        outline=tint(int(rng.integers(10, 50)), 6),
        piece_scale=float(rng.uniform(0.78, 1.04)),
    )


def render_board(
    placement: str,
    rng: np.random.Generator,
    *,
    size: int = BOARD_PX,
    perspective: bool = True,
    style: RenderStyle | None = None,
) -> RenderedBoard:
    """Render one board from a FEN piece-placement field."""
    style = style or random_style(rng)
    labels = placement_to_labels(placement)

    canvas = _draw_squares(size, style)
    _draw_pieces(canvas, labels, size, style, rng)
    _apply_lighting(canvas, rng)

    corners = np.array(
        [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32
    )
    if perspective:
        canvas, corners = _apply_perspective(canvas, corners, rng)

    _apply_camera_noise(canvas, rng)
    return RenderedBoard(canvas, placement, corners, labels)


def placement_to_labels(placement: str) -> np.ndarray:
    """FEN piece placement to 64 class indices, a8 first (FEN reading order)."""
    labels = np.zeros(64, dtype=np.int64)
    index = 0
    for char in placement:
        if char == "/":
            continue
        if char.isdigit():
            index += int(char)
            continue
        labels[index] = CLASS_INDEX[char]
        index += 1
    if index != 64:
        raise ValueError(f"placement describes {index} squares, expected 64")
    return labels


def labels_to_placement(labels: np.ndarray) -> str:
    """Inverse of placement_to_labels -- how the classifier's output becomes a FEN."""
    rows: list[str] = []
    for row in range(8):
        parts: list[str] = []
        empty = 0
        for col in range(8):
            symbol = PIECE_CLASSES[int(labels[row * 8 + col])]
            if symbol == ".":
                empty += 1
                continue
            if empty:
                parts.append(str(empty))
                empty = 0
            parts.append(symbol)
        if empty:
            parts.append(str(empty))
        rows.append("".join(parts))
    return "/".join(rows)


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------


def _draw_squares(size: int, style: RenderStyle) -> np.ndarray:
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    square = size // 8
    for row in range(8):
        for col in range(8):
            colour = style.light_square if (row + col) % 2 == 0 else style.dark_square
            canvas[row * square:(row + 1) * square, col * square:(col + 1) * square] = colour
    return canvas


def _draw_pieces(
    canvas: np.ndarray,
    labels: np.ndarray,
    size: int,
    style: RenderStyle,
    rng: np.random.Generator,
) -> None:
    square = size / 8.0
    for index, label in enumerate(labels):
        symbol = PIECE_CLASSES[int(label)]
        if symbol == ".":
            continue
        row, col = divmod(index, 8)
        # Jitter the placement: pieces in a real game are never perfectly centred,
        # and a model trained on perfect centring learns to rely on it.
        cx = (col + 0.5) * square + rng.uniform(-0.08, 0.08) * square
        cy = (row + 0.5) * square + rng.uniform(-0.06, 0.06) * square
        _draw_piece(canvas, symbol, cx, cy, square * style.piece_scale, style)


def _draw_piece(
    canvas: np.ndarray,
    symbol: str,
    cx: float,
    cy: float,
    square: float,
    style: RenderStyle,
) -> None:
    """Draw one piece as a distinct silhouette.

    Each type gets a genuinely different outline rather than a scaled version of one
    shape: the classifier has to learn shape, and near-identical glyphs would make
    the task artificially easy in a way that would not survive contact with a real
    chess set.

    Pieces are drawn standing *upward* from their square's centre, which is what
    makes board.CROP_RISE necessary and what the crop extension is calibrated against.
    """
    fill = style.white_piece if symbol.isupper() else style.black_piece
    outline = style.outline
    kind = symbol.lower()

    base_y = cy + square * 0.34
    height = square * {"p": 0.62, "n": 0.86, "b": 0.90, "r": 0.74, "q": 1.00, "k": 1.06}[kind]
    top_y = base_y - height
    radius = square * 0.26

    # Every piece stands on a base ellipse -- the footprint that marks which square
    # it occupies even when its head overlaps the square behind.
    cv2.ellipse(canvas, (int(cx), int(base_y)), (int(radius * 1.05), int(radius * 0.36)),
                0, 0, 360, fill, -1, cv2.LINE_AA)
    cv2.ellipse(canvas, (int(cx), int(base_y)), (int(radius * 1.05), int(radius * 0.36)),
                0, 0, 360, outline, 1, cv2.LINE_AA)

    body = np.array(
        [
            [cx - radius * 0.62, base_y],
            [cx - radius * 0.34, top_y + height * 0.42],
            [cx + radius * 0.34, top_y + height * 0.42],
            [cx + radius * 0.62, base_y],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(canvas, [body], fill, cv2.LINE_AA)
    cv2.polylines(canvas, [body], True, outline, 1, cv2.LINE_AA)

    head_y = int(top_y + height * 0.30)

    if kind == "p":
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.46), fill, -1, cv2.LINE_AA)
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.46), outline, 1, cv2.LINE_AA)

    elif kind == "r":
        # Battlements: a flat crenellated top.
        top = np.array(
            [
                [cx - radius * 0.72, head_y + radius * 0.34],
                [cx - radius * 0.72, head_y - radius * 0.30],
                [cx - radius * 0.36, head_y - radius * 0.30],
                [cx - radius * 0.36, head_y - radius * 0.02],
                [cx + radius * 0.36, head_y - radius * 0.02],
                [cx + radius * 0.36, head_y - radius * 0.30],
                [cx + radius * 0.72, head_y - radius * 0.30],
                [cx + radius * 0.72, head_y + radius * 0.34],
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(canvas, [top], fill, cv2.LINE_AA)
        cv2.polylines(canvas, [top], True, outline, 1, cv2.LINE_AA)

    elif kind == "n":
        # A forward-facing wedge: the knight's distinctive asymmetry.
        head = np.array(
            [
                [cx - radius * 0.62, head_y + radius * 0.42],
                [cx - radius * 0.30, head_y - radius * 0.52],
                [cx + radius * 0.70, head_y - radius * 0.16],
                [cx + radius * 0.30, head_y + radius * 0.42],
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(canvas, [head], fill, cv2.LINE_AA)
        cv2.polylines(canvas, [head], True, outline, 1, cv2.LINE_AA)

    elif kind == "b":
        # A pointed teardrop with the bishop's mitre slit.
        cv2.ellipse(canvas, (int(cx), head_y), (int(radius * 0.42), int(radius * 0.62)),
                    0, 0, 360, fill, -1, cv2.LINE_AA)
        cv2.ellipse(canvas, (int(cx), head_y), (int(radius * 0.42), int(radius * 0.62)),
                    0, 0, 360, outline, 1, cv2.LINE_AA)
        cv2.line(canvas, (int(cx + radius * 0.16), int(head_y - radius * 0.44)),
                 (int(cx - radius * 0.10), int(head_y + radius * 0.10)), outline, 1, cv2.LINE_AA)

    elif kind == "q":
        # A crown of points.
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.46), fill, -1, cv2.LINE_AA)
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.46), outline, 1, cv2.LINE_AA)
        for offset in (-0.62, -0.2, 0.2, 0.62):
            tip = (int(cx + radius * offset), int(head_y - radius * 0.82))
            cv2.line(canvas, (int(cx + radius * offset * 0.6), head_y), tip, fill, 3, cv2.LINE_AA)
            cv2.circle(canvas, tip, int(radius * 0.15), fill, -1, cv2.LINE_AA)
            cv2.circle(canvas, tip, int(radius * 0.15), outline, 1, cv2.LINE_AA)

    else:  # king
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.48), fill, -1, cv2.LINE_AA)
        cv2.circle(canvas, (int(cx), head_y), int(radius * 0.48), outline, 1, cv2.LINE_AA)
        top = int(head_y - radius * 0.98)
        cv2.line(canvas, (int(cx), head_y - int(radius * 0.30)), (int(cx), top), fill, 4, cv2.LINE_AA)
        cv2.line(canvas, (int(cx - radius * 0.34), int(top + radius * 0.26)),
                 (int(cx + radius * 0.34), int(top + radius * 0.26)), fill, 4, cv2.LINE_AA)
        cv2.line(canvas, (int(cx), head_y - int(radius * 0.30)), (int(cx), top), outline, 1, cv2.LINE_AA)


# --------------------------------------------------------------------------
# Camera simulation
# --------------------------------------------------------------------------


def _apply_lighting(canvas: np.ndarray, rng: np.random.Generator) -> None:
    """A soft directional gradient, as from a window or a lamp to one side.

    Uneven lighting is the single most common way a real board differs from a
    rendered one, and a model that has never seen it treats a shadowed corner as a
    different colour of square entirely.
    """
    height, width = canvas.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    angle = rng.uniform(0, 2 * np.pi)
    strength = rng.uniform(0.10, 0.42)

    gradient = (np.cos(angle) * xs / width) + (np.sin(angle) * ys / height)
    gradient = (gradient - gradient.min()) / max(float(np.ptp(gradient)), 1e-6)
    scale = (1.0 - strength / 2) + gradient * strength

    # Compute in float and clip before casting back. Multiplying uint8 in place
    # wraps a brightened pixel around to near-black, which shows up as lurid
    # diagonal bands across the lit half of the board.
    lit = np.clip(canvas.astype(np.float32) * scale[..., None], 0, 255)
    canvas[:] = lit.astype(np.uint8)


def _apply_perspective(
    canvas: np.ndarray, corners: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Warp as if seen from a handheld phone rather than straight above.

    The vertical squeeze is larger than the horizontal shift because that is how
    people actually hold a phone over a board -- above and tilted forward, not off
    to one side.
    """
    size = canvas.shape[0]
    margin = size * 0.5

    def jitter(scale: float) -> float:
        return float(rng.uniform(-scale, scale) * size)

    squeeze = rng.uniform(0.0, 0.16) * size  # the far edge is narrower
    destination = np.array(
        [
            [corners[0][0] + squeeze + jitter(0.03), corners[0][1] + jitter(0.03)],
            [corners[1][0] - squeeze + jitter(0.03), corners[1][1] + jitter(0.03)],
            [corners[2][0] + jitter(0.04), corners[2][1] + jitter(0.03)],
            [corners[3][0] + jitter(0.04), corners[3][1] + jitter(0.03)],
        ],
        dtype=np.float32,
    )
    destination += margin / 2

    matrix = cv2.getPerspectiveTransform(corners, destination)
    output = int(size + margin)
    warped = cv2.warpPerspective(
        canvas, matrix, (output, output), borderMode=cv2.BORDER_REPLICATE
    )
    return warped, destination


def _apply_camera_noise(canvas: np.ndarray, rng: np.random.Generator) -> None:
    """Blur and sensor noise, so the model does not depend on pristine edges."""
    blur = int(rng.integers(0, 3)) * 2 + 1
    if blur > 1:
        canvas[:] = cv2.GaussianBlur(canvas, (blur, blur), 0)

    sigma = rng.uniform(1.5, 9.0)
    noisy = canvas.astype(np.float32) + rng.normal(0, sigma, canvas.shape)
    canvas[:] = np.clip(noisy, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Position sampling
# --------------------------------------------------------------------------


def random_placement(rng: np.random.Generator) -> str:
    """A placement from a plausible game rather than a random scatter of pieces.

    Playing random legal moves from the start keeps the material counts, pawn
    structures and king positions realistic. Sampling pieces uniformly onto squares
    would train the model on boards that cannot occur, and under-represent the empty
    squares that dominate a real position.
    """
    board = chess.Board()
    for _ in range(int(rng.integers(0, 80))):
        moves = list(board.legal_moves)
        if not moves:
            break
        board.push(moves[int(rng.integers(len(moves)))])
    return board.board_fen()
