import { describe, expect, it } from 'vitest';

import { Backoff, DEFAULT_BACKOFF } from './backoff';

const always = (value: number) => () => value;

describe('Backoff', () => {
  it('grows exponentially up to the ceiling', () => {
    const backoff = new Backoff({ initialMs: 100, maxMs: 1000, factor: 2 }, always(1));
    expect([backoff.next(), backoff.next(), backoff.next(), backoff.next()]).toEqual([
      100, 200, 400, 800,
    ]);
  });

  it('never exceeds the maximum', () => {
    const backoff = new Backoff({ initialMs: 100, maxMs: 500, factor: 2 }, always(1));
    for (let i = 0; i < 20; i += 1) {
      expect(backoff.next()).toBeLessThanOrEqual(500);
    }
  });

  it('applies full jitter so clients do not retry in lockstep', () => {
    // A server restart wakes every client at once; without jitter they all retry
    // at the same instant and keep colliding.
    const low = new Backoff({ initialMs: 100, maxMs: 1000, factor: 2 }, always(0));
    const high = new Backoff({ initialMs: 100, maxMs: 1000, factor: 2 }, always(1));
    expect(low.next()).toBe(0);
    expect(high.next()).toBe(100);
  });

  it('restarts fast after a successful connection', () => {
    const backoff = new Backoff({ initialMs: 100, maxMs: 10_000, factor: 2 }, always(1));
    backoff.next();
    backoff.next();
    backoff.next();
    backoff.reset();
    expect(backoff.attempts).toBe(0);
    expect(backoff.next()).toBe(100);
  });

  it('recovers from a long outage within the default ceiling', () => {
    const backoff = new Backoff(DEFAULT_BACKOFF, always(1));
    const delays = Array.from({ length: 10 }, () => backoff.next());
    expect(Math.max(...delays)).toBe(DEFAULT_BACKOFF.maxMs);
  });
});
