/**
 * Keeping the board's corners stable across frames.
 *
 * Calibration happens once. After that the corners have to survive hand shake,
 * without drifting away from the board or jumping when a frame is misread. This is
 * the smoothing and validation layer over whatever per-frame estimate arrives --
 * optical flow from a native plugin, or a fresh detection.
 *
 * The rules it enforces:
 *
 * * **Small movements are followed, large ones are not.** A hand tremor moves the
 *   board a few pixels per frame; a tracking failure moves it half the screen. The
 *   second is always a mistake and following it would lose the board entirely.
 * * **Shape is preserved.** A board stays a board. An update that turns the quad
 *   into a sliver is a tracking failure however small the individual corner moves.
 * * **Sustained rejection means give up.** If updates keep failing, the tracker has
 *   genuinely lost the board and the user needs to recalibrate -- silently holding
 *   stale corners would mean classifying the wrong part of the image indefinitely.
 */

import { orderCorners, type Point } from './homography';

export interface TrackerOptions {
  /** Per-frame corner movement, as a fraction of the frame's smaller dimension,
   *  beyond which an update is rejected as a tracking failure rather than motion. */
  readonly maxJumpFraction: number;
  /** Exponential smoothing weight for accepted updates. Low enough to damp jitter,
   *  high enough to follow a deliberate reframe within a few frames. */
  readonly smoothing: number;
  /** Consecutive rejections before the board is considered lost. */
  readonly lostAfterRejections: number;
  /** Largest tolerated change in the quad's area, as a multiple in either
   *  direction. Deliberately tighter than the jump limit implies: it exists to
   *  catch estimates where every corner moved a plausible amount but the quad as a
   *  whole collapsed, which per-corner distance cannot see. */
  readonly maxAreaChange: number;
}

export const DEFAULT_TRACKER: TrackerOptions = {
  maxJumpFraction: 0.08,
  smoothing: 0.35,
  lostAfterRejections: 15,
  maxAreaChange: 1.3,
};

export type TrackerState = 'tracking' | 'recovering' | 'lost';

export interface TrackerUpdate {
  readonly state: TrackerState;
  readonly corners: readonly Point[];
  readonly accepted: boolean;
  readonly reason?: string;
}

export class CornerTracker {
  private corners: Point[];
  private rejections = 0;
  private state: TrackerState = 'tracking';

  constructor(
    initial: readonly Point[],
    private readonly frame: { width: number; height: number },
    private readonly options: TrackerOptions = DEFAULT_TRACKER,
  ) {
    this.corners = orderCorners(initial);
  }

  get current(): readonly Point[] {
    return this.corners;
  }

  get trackingState(): TrackerState {
    return this.state;
  }

  /** Feed a fresh per-frame estimate. Returns the corners to actually use. */
  update(candidate: readonly Point[]): TrackerUpdate {
    if (candidate.length !== 4) {
      return this.reject('estimate did not contain four corners');
    }

    const ordered = orderCorners(candidate);
    const limit = Math.min(this.frame.width, this.frame.height) * this.options.maxJumpFraction;

    const largestMove = Math.max(
      ...ordered.map((point, index) => distance(point, this.corners[index]!)),
    );
    if (largestMove > limit) {
      return this.reject('corners moved too far in one frame');
    }

    if (!this.areaIsConsistent(ordered)) {
      return this.reject('the quad changed shape too much to be the same board');
    }

    // Smoothed rather than adopted outright. A per-frame estimate carries noise
    // even when correct, and an unsmoothed crop visibly shimmers.
    const weight = this.options.smoothing;
    this.corners = ordered.map((point, index) => ({
      x: this.corners[index]!.x * (1 - weight) + point.x * weight,
      y: this.corners[index]!.y * (1 - weight) + point.y * weight,
    }));

    this.rejections = 0;
    this.state = 'tracking';
    return { state: this.state, corners: this.corners, accepted: true };
  }

  /** Adopt new corners outright, as after the user recalibrates. */
  reset(corners: readonly Point[]): void {
    this.corners = orderCorners(corners);
    this.rejections = 0;
    this.state = 'tracking';
  }

  private reject(reason: string): TrackerUpdate {
    this.rejections += 1;
    // One bad frame is noise; a run of them means the board is genuinely gone.
    this.state =
      this.rejections >= this.options.lostAfterRejections ? 'lost' : 'recovering';
    return { state: this.state, corners: this.corners, accepted: false, reason };
  }

  private areaIsConsistent(candidate: readonly Point[]): boolean {
    const before = quadArea(this.corners);
    const after = quadArea(candidate);
    if (before === 0 || after === 0) return false;
    const change = Math.max(before / after, after / before);
    return change <= this.options.maxAreaChange;
  }
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Area of the quadrilateral, by the shoelace formula.
 *
 * Area rather than an aspect ratio of longest edges: a ratio taken from the longest
 * opposing edges is blind to one edge collapsing, which is precisely the failure
 * shape this check exists to catch.
 */
function quadArea(corners: readonly Point[]): number {
  let total = 0;
  for (let i = 0; i < corners.length; i += 1) {
    const current = corners[i]!;
    const next = corners[(i + 1) % corners.length]!;
    total += current.x * next.y - next.x * current.y;
  }
  return Math.abs(total) / 2;
}
