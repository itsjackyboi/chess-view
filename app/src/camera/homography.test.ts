import { describe, expect, it } from 'vitest';

import {
  applyMatrix,
  cornersAreUsable,
  homographyBetween,
  orderCorners,
  rectifyingHomography,
  solve,
  type Point,
} from './homography';

const SQUARE: Point[] = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 100 },
  { x: 0, y: 100 },
];

const FRAME = { width: 400, height: 400 };

describe('solve', () => {
  it('solves a small system', () => {
    // x + y = 3, x - y = 1  ->  x = 2, y = 1
    const solution = solve([[1, 1], [1, -1]], [3, 1]);
    expect(solution![0]).toBeCloseTo(2);
    expect(solution![1]).toBeCloseTo(1);
  });

  it('returns null for a singular system instead of NaNs', () => {
    // NaNs here would propagate silently into every subsequent frame.
    expect(solve([[1, 1], [2, 2]], [3, 6])).toBeNull();
  });

  it('pivots so a zero leading coefficient does not break it', () => {
    const solution = solve([[0, 1], [1, 0]], [2, 3]);
    expect(solution![0]).toBeCloseTo(3);
    expect(solution![1]).toBeCloseTo(2);
  });
});

describe('homographyBetween', () => {
  it('maps each source corner onto its destination', () => {
    const destination: Point[] = [
      { x: 10, y: 20 },
      { x: 210, y: 15 },
      { x: 200, y: 190 },
      { x: 5, y: 200 },
    ];
    const matrix = homographyBetween(SQUARE, destination)!;

    SQUARE.forEach((source, index) => {
      const mapped = applyMatrix(matrix, source);
      expect(mapped.x).toBeCloseTo(destination[index]!.x, 4);
      expect(mapped.y).toBeCloseTo(destination[index]!.y, 4);
    });
  });

  it('handles a strong perspective skew', () => {
    // A shallow viewing angle: the far edge much narrower than the near one.
    const skewed: Point[] = [
      { x: 80, y: 40 },
      { x: 220, y: 40 },
      { x: 300, y: 260 },
      { x: 0, y: 260 },
    ];
    const matrix = homographyBetween(skewed, SQUARE)!;
    expect(matrix).not.toBeNull();
    expect(applyMatrix(matrix, skewed[2]!).x).toBeCloseTo(100, 3);
  });

  it('returns null for degenerate input rather than a matrix of NaNs', () => {
    const collinear: Point[] = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 20, y: 0 },
      { x: 30, y: 0 },
    ];
    expect(homographyBetween(collinear, SQUARE)).toBeNull();
  });

  it('rejects the wrong number of points', () => {
    expect(homographyBetween(SQUARE.slice(0, 3), SQUARE)).toBeNull();
  });
});

describe('rectifyingHomography', () => {
  it('flattens board corners onto a square of the requested size', () => {
    const corners: Point[] = [
      { x: 50, y: 30 },
      { x: 250, y: 40 },
      { x: 270, y: 240 },
      { x: 30, y: 230 },
    ];
    const matrix = rectifyingHomography(corners, 320)!;

    expect(applyMatrix(matrix, corners[0]!).x).toBeCloseTo(0, 3);
    expect(applyMatrix(matrix, corners[2]!).x).toBeCloseTo(319, 3);
    expect(applyMatrix(matrix, corners[2]!).y).toBeCloseTo(319, 3);
  });
});

describe('orderCorners', () => {
  it('leaves an already-ordered quad alone', () => {
    expect(orderCorners(SQUARE)).toEqual(SQUARE);
  });

  it.each([1, 2, 3])('normalises a quad rotated by %i', (shift) => {
    // Must agree with order_corners in the Python board module: a disagreement
    // would mirror the board and produce a wrong position that still looks valid.
    const rotated = [...SQUARE.slice(shift), ...SQUARE.slice(0, shift)];
    expect(orderCorners(rotated)).toEqual(SQUARE);
  });

  it('normalises a reversed winding', () => {
    expect(orderCorners([...SQUARE].reverse())).toEqual(SQUARE);
  });
});

describe('cornersAreUsable', () => {
  it('accepts a sensible board outline', () => {
    const corners: Point[] = [
      { x: 50, y: 50 },
      { x: 350, y: 60 },
      { x: 340, y: 350 },
      { x: 60, y: 340 },
    ];
    expect(cornersAreUsable(corners, FRAME)).toEqual({ ok: true });
  });

  it('rejects corners bunched into a tiny region', () => {
    // Warping this would magnify a handful of pixels into a whole board.
    const tiny: Point[] = [
      { x: 10, y: 10 },
      { x: 30, y: 10 },
      { x: 30, y: 30 },
      { x: 10, y: 30 },
    ];
    const result = cornersAreUsable(tiny, FRAME);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toMatch(/edges of the board/);
  });

  it('asks the user to raise the phone when the board is a sliver', () => {
    const shallow: Point[] = [
      { x: 20, y: 180 },
      { x: 380, y: 180 },
      { x: 380, y: 260 },
      { x: 20, y: 260 },
    ];
    const result = cornersAreUsable(shallow, FRAME);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toMatch(/above the board/);
  });

  it('rejects an incomplete set', () => {
    expect(cornersAreUsable(SQUARE.slice(0, 2), FRAME).ok).toBe(false);
  });
});
