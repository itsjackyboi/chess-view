import { describe, expect, it } from 'vitest';

import { DEFAULT_GATE, FrameGate, motionScore } from './frameGate';

const STILL = 0;
const MOVING = 0.4;

describe('FrameGate', () => {
  it('sends the first frame it sees', () => {
    expect(new FrameGate().decide(0, STILL).send).toBe(true);
  });

  it('keeps a slow keepalive while the board is still', () => {
    const gate = new FrameGate(DEFAULT_GATE);
    gate.decide(0, STILL);

    // 0.5 fps: nothing for the next two seconds.
    expect(gate.decide(500, STILL).send).toBe(false);
    expect(gate.decide(1500, STILL).send).toBe(false);
    expect(gate.decide(2000, STILL).send).toBe(true);
  });

  it('bursts while something is moving', () => {
    const gate = new FrameGate(DEFAULT_GATE);
    gate.decide(0, MOVING);

    // 5 fps: one every 200ms.
    expect(gate.decide(100, MOVING).send).toBe(false);
    expect(gate.decide(200, MOVING).send).toBe(true);
    expect(gate.decide(400, MOVING).send).toBe(true);
  });

  it('keeps bursting briefly after motion stops', () => {
    // The frames worth sending are the settled ones just after a piece lands.
    const gate = new FrameGate(DEFAULT_GATE);
    gate.decide(0, MOVING);

    expect(gate.decide(200, STILL).send).toBe(true);
    expect(gate.decide(400, STILL).send).toBe(true);
    expect(gate.decide(600, STILL).send).toBe(true);
  });

  it('drops back to idle once the hold expires', () => {
    const gate = new FrameGate(DEFAULT_GATE);
    gate.decide(0, MOVING);
    gate.decide(1400, STILL); // past burstHoldMs

    expect(gate.decide(1600, STILL).send).toBe(false);
  });

  it('flags burst frames so the server can tell them from keepalives', () => {
    const gate = new FrameGate(DEFAULT_GATE);
    expect(gate.decide(0, MOVING).motion).toBe(true);

    const idle = new FrameGate(DEFAULT_GATE);
    expect(idle.decide(0, STILL).motion).toBe(false);
  });

  it('sends a small fraction of a 30fps feed over a mostly-still minute', () => {
    // The bandwidth claim in docs/architecture.md, checked directly.
    const gate = new FrameGate(DEFAULT_GATE);
    const frameMs = 1000 / 30;

    for (let frame = 0; frame < 30 * 60; frame += 1) {
      const now = frame * frameMs;
      // A two-second move every fifteen seconds; still otherwise.
      const inMove = now % 15_000 < 2_000;
      gate.decide(now, inMove ? MOVING : STILL);
    }

    expect(gate.sendRate).toBeLessThan(0.08);
    // Still frequent enough to catch every move.
    expect(gate.stats.sent).toBeGreaterThan(60);
  });
});

describe('motionScore', () => {
  const frame = (fill: number, size = 256) => new Uint8Array(size).fill(fill);

  it('treats a missing previous frame as motion', () => {
    // The first frame of a session has nothing to compare against, and must not be
    // mistaken for a still board.
    expect(motionScore(null, frame(0))).toBe(1);
  });

  it('reports no motion for an identical frame', () => {
    expect(motionScore(frame(120), frame(120))).toBe(0);
  });

  it('ignores sensor noise below the threshold', () => {
    expect(motionScore(frame(120), frame(130))).toBe(0);
  });

  it('reports motion for a substantially changed frame', () => {
    expect(motionScore(frame(0), frame(255))).toBe(1);
  });

  it('scales with the proportion of the frame that changed', () => {
    const previous = frame(0);
    const current = frame(0);
    // Change a quarter of the sampled pixels (every 16th is sampled).
    for (let i = 0; i < current.length; i += 16) {
      if ((i / 16) % 4 === 0) current[i] = 255;
    }
    expect(motionScore(previous, current)).toBeCloseTo(0.25, 2);
  });

  it('treats a resized frame as motion rather than comparing garbage', () => {
    expect(motionScore(frame(0, 128), frame(0, 256))).toBe(1);
  });
});
