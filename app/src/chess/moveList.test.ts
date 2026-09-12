import { describe, expect, it } from 'vitest';

import type { MoveRecord } from '~/state/reducer';

import { pairMoves } from './moveList';

const move = (ply: number, san: string): MoveRecord => ({ ply, san, fen: '' });

describe('pairMoves', () => {
  it('pairs white and black moves under one number', () => {
    expect(pairMoves([move(1, 'e4'), move(2, 'e5'), move(3, 'Nf3')])).toEqual([
      { number: 1, white: 'e4', black: 'e5' },
      { number: 2, white: 'Nf3' },
    ]);
  });

  it('handles an empty history', () => {
    expect(pairMoves([])).toEqual([]);
  });

  it('numbers from the ply, so a resumed game does not restart at 1', () => {
    // History that begins mid-game after a correction or a reconnect.
    expect(pairMoves([move(9, 'Bb5'), move(10, 'a6')])).toEqual([
      { number: 5, white: 'Bb5', black: 'a6' },
    ]);
  });

  it('starts a pair on black when the history begins there', () => {
    expect(pairMoves([move(2, 'e5'), move(3, 'Nf3')])).toEqual([
      { number: 1, black: 'e5' },
      { number: 2, white: 'Nf3' },
    ]);
  });
});
