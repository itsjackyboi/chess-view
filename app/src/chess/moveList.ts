/**
 * Presenting move history the way a scoresheet reads.
 */

import type { MoveRecord } from '~/state/reducer';

export interface MovePair {
  readonly number: number;
  readonly white?: string;
  readonly black?: string;
}

/**
 * Group half-moves into numbered pairs.
 *
 * Works from each record's ply rather than from its index, so a history that starts
 * mid-game -- after a manual correction, or a resumed session -- still numbers
 * correctly instead of restarting at 1.
 */
export function pairMoves(history: readonly MoveRecord[]): MovePair[] {
  const pairs: { number: number; white?: string; black?: string }[] = [];

  for (const record of history) {
    // Ply 1 is white's first move, so each pair spans two plies.
    const number = Math.floor((record.ply - 1) / 2) + 1;
    const isWhite = record.ply % 2 === 1;

    let pair = pairs[pairs.length - 1];
    if (!pair || pair.number !== number) {
      pair = { number };
      pairs.push(pair);
    }
    if (isWhite) pair.white = record.san;
    else pair.black = record.san;
  }

  return pairs;
}
