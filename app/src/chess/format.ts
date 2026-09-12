/**
 * Turning engine output into something readable at a glance.
 *
 * Everything here takes scores already normalised to white's point of view by the
 * server, so positive is always good for white regardless of whose turn it is.
 */

import type { EvalLine } from '@chessview/protocol';

/**
 * Centipawns to a 0..1 bar fraction, where 1 is winning for white.
 *
 * A linear mapping would be useless: the difference between +1 and +2 pawns matters
 * enormously and the difference between +15 and +16 not at all. This is the logistic
 * win-probability curve used by Lichess, which spends the bar's travel where the
 * evaluation is actually informative.
 */
export function scoreToBarFraction(line: EvalLine | undefined): number {
  if (!line) return 0.5;

  if (line.scoreMate != null) {
    // Mate is terminal, so the bar goes all the way. Mate in 0 shouldn't happen,
    // but treat it as decided rather than as a draw.
    return line.scoreMate > 0 ? 1 : 0;
  }
  if (line.scoreCp == null) return 0.5;

  const winProbability = 2 / (1 + Math.exp(-0.00368208 * line.scoreCp)) - 1;
  return clamp((winProbability + 1) / 2, 0, 1);
}

/** The score as shown to the user: `+1.25`, `-0.40`, `M3`, `-M2`. */
export function formatScore(line: EvalLine | undefined): string {
  if (!line) return '--';

  if (line.scoreMate != null) {
    if (line.scoreMate === 0) return '#';
    return line.scoreMate > 0 ? `M${line.scoreMate}` : `-M${Math.abs(line.scoreMate)}`;
  }
  if (line.scoreCp == null) return '--';

  const pawns = line.scoreCp / 100;
  // An explicit sign on the positive side: "0.30" alone reads as neutral.
  return `${pawns >= 0 ? '+' : ''}${pawns.toFixed(2)}`;
}

/**
 * A principal variation as numbered move pairs: `14. Nf3 Nc6 15. Bb5`.
 *
 * `startPly` is the number of half-moves already played, which is what fixes the
 * numbering and tells us whether the line opens on white's or black's move.
 */
export function formatLine(san: string[], startPly: number, maxPlies = 6): string {
  const parts: string[] = [];
  const shown = san.slice(0, maxPlies);

  shown.forEach((move, index) => {
    const ply = startPly + index;
    const moveNumber = Math.floor(ply / 2) + 1;
    const isWhite = ply % 2 === 0;

    if (isWhite) {
      parts.push(`${moveNumber}. ${move}`);
    } else if (index === 0) {
      // A line starting on black's move needs the ellipsis to read correctly.
      parts.push(`${moveNumber}... ${move}`);
    } else {
      parts.push(move);
    }
  });

  return parts.join(' ');
}

/** Whose move it is, read from a FEN. */
export function sideToMove(fen: string): 'white' | 'black' {
  return fen.split(' ')[1] === 'b' ? 'black' : 'white';
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
