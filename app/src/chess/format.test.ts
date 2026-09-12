import { describe, expect, it } from 'vitest';

import type { EvalLine } from '@chessview/protocol';

import { formatLine, formatScore, scoreToBarFraction, sideToMove } from './format';

const cp = (scoreCp: number): EvalLine => ({ multipv: 1, scoreCp, pv: [], san: [] });
const mate = (scoreMate: number): EvalLine => ({ multipv: 1, scoreMate, pv: [], san: [] });

describe('formatScore', () => {
  it('shows pawns to two places with an explicit sign', () => {
    // Without the plus, a small advantage reads as neutral.
    expect(formatScore(cp(125))).toBe('+1.25');
    expect(formatScore(cp(-40))).toBe('-0.40');
    expect(formatScore(cp(0))).toBe('+0.00');
  });

  it('shows mate distances', () => {
    expect(formatScore(mate(3))).toBe('M3');
    expect(formatScore(mate(-2))).toBe('-M2');
  });

  it('renders nothing rather than a wrong number when there is no score', () => {
    expect(formatScore(undefined)).toBe('--');
    expect(formatScore({ multipv: 1, pv: [], san: [] })).toBe('--');
  });
});

describe('scoreToBarFraction', () => {
  it('puts an equal position in the middle', () => {
    expect(scoreToBarFraction(cp(0))).toBeCloseTo(0.5, 5);
  });

  it('is symmetric about equality', () => {
    expect(scoreToBarFraction(cp(300)) + scoreToBarFraction(cp(-300))).toBeCloseTo(1, 5);
  });

  it('moves monotonically with the score', () => {
    const points = [-900, -300, -100, 0, 100, 300, 900].map((c) => scoreToBarFraction(cp(c)));
    expect(points).toEqual([...points].sort((a, b) => a - b));
  });

  it('spends the bar travel where the evaluation is informative', () => {
    // A pawn matters near equality and barely registers when already winning --
    // a linear mapping would waste most of the bar.
    const nearEqual = scoreToBarFraction(cp(100)) - scoreToBarFraction(cp(0));
    const alreadyWinning = scoreToBarFraction(cp(1600)) - scoreToBarFraction(cp(1500));
    expect(nearEqual).toBeGreaterThan(alreadyWinning * 5);
  });

  it('pins the bar for a forced mate', () => {
    expect(scoreToBarFraction(mate(4))).toBe(1);
    expect(scoreToBarFraction(mate(-4))).toBe(0);
  });

  it('stays in range for extreme scores', () => {
    for (const score of [-100000, -5000, 5000, 100000]) {
      const fraction = scoreToBarFraction(cp(score));
      expect(fraction).toBeGreaterThanOrEqual(0);
      expect(fraction).toBeLessThanOrEqual(1);
    }
  });

  it('sits at equality when there is nothing to show', () => {
    expect(scoreToBarFraction(undefined)).toBe(0.5);
  });
});

describe('formatLine', () => {
  it('numbers a line starting on white', () => {
    expect(formatLine(['e4', 'e5', 'Nf3'], 0)).toBe('1. e4 e5 2. Nf3');
  });

  it('marks a line starting on black with an ellipsis', () => {
    expect(formatLine(['e5', 'Nf3'], 1)).toBe('1... e5 2. Nf3');
  });

  it('continues the numbering from the middle of a game', () => {
    expect(formatLine(['Bb5', 'a6'], 8)).toBe('5. Bb5 a6');
  });

  it('truncates to keep the overlay readable', () => {
    const long = ['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', 'Ba4', 'Nf6'];
    expect(formatLine(long, 0, 4)).toBe('1. e4 e5 2. Nf3 Nc6');
  });

  it('handles an empty line', () => {
    expect(formatLine([], 0)).toBe('');
  });
});

describe('sideToMove', () => {
  it('reads the side to move from a FEN', () => {
    expect(sideToMove('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1')).toBe('white');
    expect(sideToMove('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1')).toBe('black');
  });
});
