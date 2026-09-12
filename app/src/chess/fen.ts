/**
 * FEN manipulation for the manual correction board.
 *
 * The client edits only the *placement* field -- the part before the first space.
 * Side to move, castling rights and the rest are game state the server owns, and a
 * correction should replace what the camera got wrong without also overwriting
 * what the server knows and the camera cannot see.
 */

export type Piece = 'P' | 'N' | 'B' | 'R' | 'Q' | 'K' | 'p' | 'n' | 'b' | 'r' | 'q' | 'k';
export type Square = Piece | null;

export const PIECES: readonly Piece[] = ['P', 'N', 'B', 'R', 'Q', 'K', 'p', 'n', 'b', 'r', 'q', 'k'];

export const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

/** Index 0 is a8 and index 63 is h1, matching FEN reading order and the server. */
export function parsePlacement(fen: string): Square[] {
  const placement = fen.split(' ')[0] ?? '';
  const squares: Square[] = new Array(64).fill(null);

  let index = 0;
  for (const char of placement) {
    if (char === '/') continue;
    if (index >= 64) break;

    const skip = Number.parseInt(char, 10);
    if (!Number.isNaN(skip)) {
      index += skip;
      continue;
    }
    squares[index] = char as Piece;
    index += 1;
  }
  return squares;
}

export function toPlacement(squares: readonly Square[]): string {
  const rows: string[] = [];

  for (let row = 0; row < 8; row += 1) {
    let text = '';
    let empty = 0;

    for (let col = 0; col < 8; col += 1) {
      const piece = squares[row * 8 + col] ?? null;
      if (piece === null) {
        empty += 1;
        continue;
      }
      if (empty > 0) {
        text += String(empty);
        empty = 0;
      }
      text += piece;
    }
    if (empty > 0) text += String(empty);
    rows.push(text);
  }
  return rows.join('/');
}

/**
 * Replace the placement field of a FEN, keeping every other field intact.
 *
 * Preserving the tail is the point: whose move it is, castling rights and the en
 * passant square are not visible to a camera, so a correction must not clobber them.
 */
export function withPlacement(fen: string, squares: readonly Square[]): string {
  const fields = fen.split(' ');
  fields[0] = toPlacement(squares);
  // Fall back to sane defaults when handed a bare placement rather than a full FEN.
  return [fields[0], fields[1] ?? 'w', fields[2] ?? '-', fields[3] ?? '-', fields[4] ?? '0', fields[5] ?? '1'].join(' ');
}

export function setSquare(squares: readonly Square[], index: number, piece: Square): Square[] {
  const next = [...squares];
  next[index] = piece;
  return next;
}

/** Algebraic name for a board index, e.g. 0 -> 'a8', 63 -> 'h1'. */
export function squareName(index: number): string {
  const file = 'abcdefgh'[index % 8] ?? '?';
  const rank = 8 - Math.floor(index / 8);
  return `${file}${rank}`;
}

export function isLightSquare(index: number): boolean {
  const row = Math.floor(index / 8);
  const col = index % 8;
  return (row + col) % 2 === 0;
}

/**
 * Whether a placement could be a real chess position.
 *
 * A cheap guard only -- it catches the mistakes a user can make with the editor
 * (deleting a king, cloning a second one) before a correction is sent. The server
 * validates properly; this exists so the button can be disabled with a reason
 * rather than the correction bouncing back as an error.
 */
export function placementProblem(squares: readonly Square[]): string | null {
  const count = (piece: Piece) => squares.filter((square) => square === piece).length;

  const whiteKings = count('K');
  const blackKings = count('k');
  if (whiteKings !== 1) return whiteKings === 0 ? 'White needs a king' : 'Too many white kings';
  if (blackKings !== 1) return blackKings === 0 ? 'Black needs a king' : 'Too many black kings';

  // Pawns cannot stand on the first or last rank -- they would have promoted.
  for (let index = 0; index < 8; index += 1) {
    if (squares[index] === 'P' || squares[index] === 'p') return 'A pawn cannot be on the 8th rank';
  }
  for (let index = 56; index < 64; index += 1) {
    if (squares[index] === 'P' || squares[index] === 'p') return 'A pawn cannot be on the 1st rank';
  }

  return null;
}
