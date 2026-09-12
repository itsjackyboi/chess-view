/**
 * Reconnection timing.
 *
 * Exponential with full jitter. The jitter matters more than the exponent: if a
 * server restarts, every client that was connected to it tries to reconnect at the
 * same moment, and an unjittered backoff has them all retry in lockstep forever.
 */

export interface BackoffOptions {
  readonly initialMs: number;
  readonly maxMs: number;
  readonly factor: number;
}

export const DEFAULT_BACKOFF: BackoffOptions = {
  // Short enough that a brief blip is invisible to the user...
  initialMs: 500,
  // ...and capped low enough that recovery from a long outage still feels prompt.
  maxMs: 15_000,
  factor: 2,
};

export class Backoff {
  private attempt = 0;

  constructor(
    private readonly options: BackoffOptions = DEFAULT_BACKOFF,
    /** Injectable for tests; production uses Math.random. */
    private readonly random: () => number = Math.random,
  ) {}

  /** Milliseconds to wait before the next attempt, then advance. */
  next(): number {
    const ceiling = Math.min(
      this.options.maxMs,
      this.options.initialMs * this.options.factor ** this.attempt,
    );
    this.attempt += 1;
    // Full jitter: uniform across [0, ceiling] rather than a band around it.
    return Math.round(this.random() * ceiling);
  }

  /** Call after a connection succeeds so the next outage starts fast again. */
  reset(): void {
    this.attempt = 0;
  }

  get attempts(): number {
    return this.attempt;
  }
}
