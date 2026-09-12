import { describe, expect, it } from 'vitest';

import { CornerTracker, DEFAULT_TRACKER } from './cornerTracker';
import type { Point } from './homography';

const FRAME = { width: 400, height: 400 };

const SQUARE: Point[] = [
  { x: 100, y: 100 },
  { x: 300, y: 100 },
  { x: 300, y: 300 },
  { x: 100, y: 300 },
];

const shift = (corners: readonly Point[], dx: number, dy: number): Point[] =>
  corners.map((c) => ({ x: c.x + dx, y: c.y + dy }));

describe('CornerTracker', () => {
  it('starts out tracking the corners it was given', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    expect(tracker.trackingState).toBe('tracking');
    expect(tracker.current).toEqual(SQUARE);
  });

  it('follows small movement, as from hand shake', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    const result = tracker.update(shift(SQUARE, 3, -2));

    expect(result.accepted).toBe(true);
    expect(result.state).toBe('tracking');
    // Smoothed, so it moves partway rather than snapping.
    expect(result.corners[0]!.x).toBeGreaterThan(100);
    expect(result.corners[0]!.x).toBeLessThan(103);
  });

  it('converges on a sustained reframe within a few frames', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    const target = shift(SQUARE, 10, 10);
    for (let i = 0; i < 15; i += 1) tracker.update(target);

    expect(tracker.current[0]!.x).toBeCloseTo(110, 0);
  });

  it('rejects a jump that could only be a tracking failure', () => {
    // Half the screen in one frame is never real board movement, and following it
    // would lose the board entirely.
    const tracker = new CornerTracker(SQUARE, FRAME);
    const result = tracker.update(shift(SQUARE, 200, 0));

    expect(result.accepted).toBe(false);
    expect(result.reason).toMatch(/moved too far/);
    expect(result.corners).toEqual(SQUARE);
  });

  it('rejects a quad that shrank, even when every corner moved a legal amount', () => {
    // The case per-corner distance cannot see: each corner moves ~28px, inside the
    // 32px jump limit, but the quad loses a third of its area. Something that
    // happens to all four corners at once is a tracking failure, not board motion.
    const tracker = new CornerTracker(SQUARE, FRAME);
    const shrunk: Point[] = [
      { x: 120, y: 120 },
      { x: 280, y: 120 },
      { x: 280, y: 280 },
      { x: 120, y: 280 },
    ];
    const result = tracker.update(shrunk);

    expect(result.accepted).toBe(false);
    expect(result.reason).toMatch(/changed shape/);
  });

  it('accepts a modest scale change, as when the phone moves closer', () => {
    // The area check must not fire on ordinary movement toward the board.
    const tracker = new CornerTracker(SQUARE, FRAME);
    const slightlyCloser: Point[] = [
      { x: 95, y: 95 },
      { x: 305, y: 95 },
      { x: 305, y: 305 },
      { x: 95, y: 305 },
    ];
    expect(tracker.update(slightlyCloser).accepted).toBe(true);
  });

  it('rejects a malformed estimate', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    expect(tracker.update(SQUARE.slice(0, 3)).accepted).toBe(false);
  });

  it('reports recovering after a single bad frame, not lost', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    expect(tracker.update(shift(SQUARE, 300, 0)).state).toBe('recovering');
  });

  it('gives up after sustained failure so the user can recalibrate', () => {
    // Silently holding stale corners would mean classifying the wrong part of the
    // image indefinitely.
    const tracker = new CornerTracker(SQUARE, FRAME);
    for (let i = 0; i < DEFAULT_TRACKER.lostAfterRejections; i += 1) {
      tracker.update(shift(SQUARE, 300, 0));
    }
    expect(tracker.trackingState).toBe('lost');
  });

  it('recovers when good frames return before the limit', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    tracker.update(shift(SQUARE, 300, 0));
    tracker.update(shift(SQUARE, 300, 0));

    expect(tracker.update(shift(SQUARE, 2, 2)).state).toBe('tracking');
  });

  it('adopts recalibrated corners outright', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    for (let i = 0; i < DEFAULT_TRACKER.lostAfterRejections; i += 1) {
      tracker.update(shift(SQUARE, 300, 0));
    }

    const fresh = shift(SQUARE, 50, 50);
    tracker.reset(fresh);
    expect(tracker.trackingState).toBe('tracking');
    expect(tracker.current).toEqual(fresh);
  });

  it('normalises corner order so a reordered estimate is not seen as a jump', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    const rotated = [...SQUARE.slice(2), ...SQUARE.slice(0, 2)];
    expect(tracker.update(rotated).accepted).toBe(true);
  });

  it('damps jitter around a stationary board', () => {
    const tracker = new CornerTracker(SQUARE, FRAME);
    let maximumDrift = 0;
    for (let i = 0; i < 60; i += 1) {
      const noisy = SQUARE.map((c) => ({
        x: c.x + (i % 2 === 0 ? 4 : -4),
        y: c.y + (i % 3 === 0 ? 3 : -3),
      }));
      const result = tracker.update(noisy);
      maximumDrift = Math.max(
        maximumDrift,
        Math.abs(result.corners[0]!.x - 100),
        Math.abs(result.corners[0]!.y - 100),
      );
    }
    // Well under the raw noise amplitude: the crop must not shimmer.
    expect(maximumDrift).toBeLessThan(4);
  });
});
