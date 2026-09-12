/**
 * Homography from four corner correspondences.
 *
 * The transform that maps the board's four corners, as the user placed them on the
 * camera preview, onto a flat square. Computing it here rather than server-side is
 * what lets the phone send only the rectified board: the room never leaves the
 * device, and the uplink carries a 320x320 crop instead of a whole frame.
 *
 * ## What this module does and does not do
 *
 * It produces the *matrix*. Applying it to pixels at camera frame rate needs a
 * native frame-processor plugin -- a per-pixel warp in JavaScript would not come
 * close to keeping up. `NativeWarp` below is the interface that plugin must satisfy;
 * until it exists the client sends downscaled full frames and the server rectifies,
 * which works and is less accurate. See docs/roadmap.md, M3.
 */

export interface Point {
  readonly x: number;
  readonly y: number;
}

/** Row-major 3x3. */
export type Matrix3 = readonly [number, number, number, number, number, number, number, number, number];

export const IDENTITY: Matrix3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];

/**
 * Solve a dense linear system by Gauss-Jordan elimination with partial pivoting.
 *
 * Eight equations is small enough that a general solver is not worth a dependency,
 * and partial pivoting is what keeps it stable when the user places corners nearly
 * collinear -- which happens whenever the board is viewed at a shallow angle.
 *
 * Returns null when the system is singular, rather than returning NaNs that would
 * propagate silently into every frame.
 */
export function solve(matrix: number[][], rhs: number[]): number[] | null {
  const size = rhs.length;
  const augmented = matrix.map((row, index) => [...row, rhs[index] as number]);

  for (let col = 0; col < size; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < size; row += 1) {
      if (Math.abs(augmented[row]![col]!) > Math.abs(augmented[pivot]![col]!)) pivot = row;
    }
    if (Math.abs(augmented[pivot]![col]!) < 1e-10) return null;

    [augmented[col], augmented[pivot]] = [augmented[pivot]!, augmented[col]!];

    const divisor = augmented[col]![col]!;
    for (let k = col; k <= size; k += 1) augmented[col]![k]! /= divisor;

    for (let row = 0; row < size; row += 1) {
      if (row === col) continue;
      const factor = augmented[row]![col]!;
      if (factor === 0) continue;
      for (let k = col; k <= size; k += 1) {
        augmented[row]![k]! -= factor * augmented[col]![k]!;
      }
    }
  }

  return augmented.map((row) => row[size] as number);
}

/**
 * The homography mapping `source` (four points, clockwise from top-left) onto
 * `destination`.
 *
 * Eight unknowns: the matrix is defined up to scale, so h22 is fixed at 1 and the
 * remaining eight solved from the four point pairs (two equations each).
 */
export function homographyBetween(
  source: readonly Point[],
  destination: readonly Point[],
): Matrix3 | null {
  if (source.length !== 4 || destination.length !== 4) return null;

  const rows: number[][] = [];
  const rhs: number[] = [];

  for (let i = 0; i < 4; i += 1) {
    const s = source[i]!;
    const d = destination[i]!;
    rows.push([s.x, s.y, 1, 0, 0, 0, -d.x * s.x, -d.x * s.y]);
    rhs.push(d.x);
    rows.push([0, 0, 0, s.x, s.y, 1, -d.y * s.x, -d.y * s.y]);
    rhs.push(d.y);
  }

  const solution = solve(rows, rhs);
  if (!solution || solution.some((value) => !Number.isFinite(value))) return null;

  return [...solution, 1] as unknown as Matrix3;
}

/** The homography flattening four board corners onto a `size`-pixel square. */
export function rectifyingHomography(corners: readonly Point[], size: number): Matrix3 | null {
  return homographyBetween(corners, [
    { x: 0, y: 0 },
    { x: size - 1, y: 0 },
    { x: size - 1, y: size - 1 },
    { x: 0, y: size - 1 },
  ]);
}

export function applyMatrix(matrix: Matrix3, point: Point): Point {
  const [a, b, c, d, e, f, g, h, i] = matrix;
  const w = g * point.x + h * point.y + i;
  if (Math.abs(w) < 1e-12) return { x: NaN, y: NaN };
  return {
    x: (a * point.x + b * point.y + c) / w,
    y: (d * point.x + e * point.y + f) / w,
  };
}

/**
 * Order four points top-left, top-right, bottom-right, bottom-left.
 *
 * Must match `order_corners` in services/vision/chessview_vision/board.py. A
 * disagreement between the two would rotate or mirror the board, producing a
 * plausible-looking but entirely wrong position.
 */
export function orderCorners(points: readonly Point[]): Point[] {
  if (points.length !== 4) return [...points];

  const bySum = [...points].sort((p, q) => p.x + p.y - (q.x + q.y));
  const byDiff = [...points].sort((p, q) => p.y - p.x - (q.y - q.x));

  const topLeft = bySum[0]!;
  const bottomRight = bySum[3]!;
  const topRight = byDiff[0]!;
  const bottomLeft = byDiff[3]!;

  return [topLeft, topRight, bottomRight, bottomLeft];
}

/**
 * Whether four corners describe a board we can usefully rectify.
 *
 * Catches the two ways calibration goes wrong: corners dragged into a sliver (a
 * shallow angle, or a mis-drag) and corners so close together that the warp would
 * magnify a handful of pixels into a whole board.
 */
export function cornersAreUsable(
  corners: readonly Point[],
  frame: { width: number; height: number },
): { ok: true } | { ok: false; reason: string } {
  if (corners.length !== 4) return { ok: false, reason: 'Place all four corners' };

  const ordered = orderCorners(corners);
  const distance = (p: Point, q: Point) => Math.hypot(p.x - q.x, p.y - q.y);

  const top = distance(ordered[0]!, ordered[1]!);
  const bottom = distance(ordered[3]!, ordered[2]!);
  const left = distance(ordered[0]!, ordered[3]!);
  const right = distance(ordered[1]!, ordered[2]!);

  const shortest = Math.min(top, bottom, left, right);
  const smallestDimension = Math.min(frame.width, frame.height);
  if (shortest < smallestDimension * 0.15) {
    return { ok: false, reason: 'Move the corners to the edges of the board' };
  }

  const width = Math.max(top, bottom);
  const height = Math.max(left, right);
  const ratio = Math.max(width / height, height / width);
  if (ratio > 2.5) {
    return { ok: false, reason: 'Hold the phone more directly above the board' };
  }

  return { ok: true };
}
