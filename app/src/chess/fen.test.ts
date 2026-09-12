import { describe, expect, it } from 'vitest';

import {
  STARTING_FEN,
  parsePlacement,
  placementProblem,
  setSquare,
  squareName,
  toPlacement,
  withPlacement,
  type Square,
} from './fen';

const empty = (): Square[] => new Array(64).fill(null);

describe('parsePlacement', () => {
  it('reads the starting position in FEN order', () => {
    const squares = parsePlacement(STARTING_FEN);
    expect(squares[0]).toBe('r'); // a8
    expect(squares[63]).toBe('R'); // h1
    expect(squares[32]).toBeNull(); // a4
  });

  it('expands empty-square runs', () => {
    expect(parsePlacement('8/8/8/8/8/8/8/8').every((s) => s === null)).toBe(true);
  });

  it('accepts a bare placement without the trailing FEN fields', () => {
    expect(parsePlacement('8/8/8/8/8/8/8/7R')[63]).toBe('R');
  });

  it('ignores content past the 64th square rather than overflowing', () => {
    expect(parsePlacement('8/8/8/8/8/8/8/8/8/8')).toHaveLength(64);
  });
});

describe('toPlacement', () => {
  it('round-trips the starting position', () => {
    expect(toPlacement(parsePlacement(STARTING_FEN))).toBe(STARTING_FEN.split(' ')[0]);
  });

  it('collapses empty squares into counts', () => {
    expect(toPlacement(empty())).toBe('8/8/8/8/8/8/8/8');
  });

  it('mixes pieces and counts correctly', () => {
    const squares = empty();
    squares[0] = 'r';
    squares[7] = 'k';
    expect(toPlacement(squares).split('/')[0]).toBe('r6k');
  });

  it('round-trips an arbitrary position', () => {
    const placement = 'r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R';
    expect(toPlacement(parsePlacement(placement))).toBe(placement);
  });
});

describe('withPlacement', () => {
  it('keeps the fields a camera cannot see', () => {
    // Side to move, castling rights and en passant are server state; a correction
    // fixes what the camera misread, not what it never observed.
    const fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq e3 5 12';
    const squares = setSquare(parsePlacement(fen), 0, null);
    const updated = withPlacement(fen, squares);

    expect(updated.split(' ').slice(1)).toEqual(['b', 'KQkq', 'e3', '5', '12']);
    expect(updated.split(' ')[0]).toBe('1nbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR');
  });

  it('supplies defaults when given only a placement', () => {
    expect(withPlacement('8/8/8/8/8/8/8/8', empty())).toBe('8/8/8/8/8/8/8/8 w - - 0 1');
  });
});

describe('setSquare', () => {
  it('does not mutate the input', () => {
    const squares = parsePlacement(STARTING_FEN);
    const next = setSquare(squares, 0, 'Q');
    expect(squares[0]).toBe('r');
    expect(next[0]).toBe('Q');
  });

  it('clears a square when given null', () => {
    expect(setSquare(parsePlacement(STARTING_FEN), 0, null)[0]).toBeNull();
  });
});

describe('squareName', () => {
  it('names the corners', () => {
    expect(squareName(0)).toBe('a8');
    expect(squareName(7)).toBe('h8');
    expect(squareName(56)).toBe('a1');
    expect(squareName(63)).toBe('h1');
  });
});

describe('placementProblem', () => {
  it('accepts a legal-looking position', () => {
    expect(placementProblem(parsePlacement(STARTING_FEN))).toBeNull();
  });

  it('catches a missing king before the correction is sent', () => {
    const squares = parsePlacement(STARTING_FEN);
    expect(placementProblem(setSquare(squares, 4, null))).toMatch(/[Bb]lack needs a king/);
  });

  it('catches a duplicated king', () => {
    const squares = setSquare(parsePlacement(STARTING_FEN), 32, 'K');
    expect(placementProblem(squares)).toMatch(/Too many white kings/);
  });

  it('catches a pawn on a promotion rank', () => {
    const squares = empty();
    squares[4] = 'k';
    squares[60] = 'K';
    squares[0] = 'P';
    expect(placementProblem(squares)).toMatch(/8th rank/);
  });

  it('catches a pawn on the first rank', () => {
    const squares = empty();
    squares[4] = 'k';
    squares[60] = 'K';
    squares[56] = 'p';
    expect(placementProblem(squares)).toMatch(/1st rank/);
  });
});
