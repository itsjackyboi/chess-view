/**
 * Decides which camera frames are worth sending.
 *
 * During a real game the board is static about 95% of the time, so streaming at a
 * constant rate mostly pays to send the same image over and over. This gate streams
 * on *events* instead: a slow keepalive while nothing moves, bursting to a high rate
 * while something does.
 *
 * That is the difference between roughly 80 MB/hr and roughly 15 MB/hr of uplink,
 * and it costs nothing in accuracy -- every frame it drops is a duplicate of one
 * already sent. See docs/architecture.md.
 */

export interface FrameGateOptions {
  /** Frames per second while the board is still. Low, but non-zero: it catches
   *  drift and a move that somehow produced no detected motion. */
  readonly idleFps: number;
  /** Frames per second while something is moving. Three agreeing frames at this
   *  rate is the ~600 ms confirmation window the latency budget allows. */
  readonly burstFps: number;
  /** How long to keep bursting after motion stops, so the frames that actually
   *  matter -- the settled board right after a piece lands -- are captured. */
  readonly burstHoldMs: number;
  /** Fraction of changed pixels above which a frame counts as motion. */
  readonly motionThreshold: number;
}

export const DEFAULT_GATE: FrameGateOptions = {
  idleFps: 0.5,
  burstFps: 5,
  burstHoldMs: 1200,
  motionThreshold: 0.02,
};

export interface GateDecision {
  readonly send: boolean;
  /** Passed to the server so it can tell a keepalive from a move burst. */
  readonly motion: boolean;
}

export class FrameGate {
  private lastSentAt = Number.NEGATIVE_INFINITY;
  private lastMotionAt = Number.NEGATIVE_INFINITY;
  private sent = 0;
  private considered = 0;

  constructor(private readonly options: FrameGateOptions = DEFAULT_GATE) {}

  /**
   * @param now         monotonic milliseconds
   * @param motionScore fraction of the frame that changed since the last one
   */
  decide(now: number, motionScore: number): GateDecision {
    this.considered += 1;

    const isMotion = motionScore >= this.options.motionThreshold;
    if (isMotion) this.lastMotionAt = now;

    // Stay in burst mode for a while after motion stops: the frames worth sending
    // are the settled ones just after a piece lands, not the blurred ones during.
    const bursting = now - this.lastMotionAt <= this.options.burstHoldMs;
    const fps = bursting ? this.options.burstFps : this.options.idleFps;
    const minimumGap = 1000 / fps;

    if (now - this.lastSentAt < minimumGap) {
      return { send: false, motion: isMotion };
    }

    this.lastSentAt = now;
    this.sent += 1;
    return { send: true, motion: bursting };
  }

  /** Frames sent as a fraction of frames considered -- the bandwidth saving. */
  get sendRate(): number {
    return this.considered === 0 ? 0 : this.sent / this.considered;
  }

  get stats(): { sent: number; considered: number } {
    return { sent: this.sent, considered: this.considered };
  }

  reset(): void {
    this.lastSentAt = Number.NEGATIVE_INFINITY;
    this.lastMotionAt = Number.NEGATIVE_INFINITY;
    this.sent = 0;
    this.considered = 0;
  }
}

/**
 * Fraction of pixels that changed between two greyscale buffers.
 *
 * Sampled on a stride rather than compared pixel by pixel: this runs on every
 * camera frame at 30 fps, and a full comparison would cost more battery than the
 * streaming it exists to avoid. Every 16th pixel is ample for detecting a hand.
 */
export function motionScore(
  previous: Uint8Array | null,
  current: Uint8Array,
  stride = 16,
  /** Per-pixel difference counted as a change, out of 255. Above camera sensor
   *  noise, below a real change in the scene. */
  pixelDelta = 18,
): number {
  if (!previous || previous.length !== current.length) return 1;

  let changed = 0;
  let sampled = 0;
  for (let i = 0; i < current.length; i += stride) {
    sampled += 1;
    if (Math.abs((current[i] ?? 0) - (previous[i] ?? 0)) > pixelDelta) changed += 1;
  }
  return sampled === 0 ? 0 : changed / sampled;
}
