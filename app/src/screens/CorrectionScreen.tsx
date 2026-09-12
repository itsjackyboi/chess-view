import { useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View, useWindowDimensions } from 'react-native';

import { BoardDiagram } from '~/components/BoardDiagram';
import {
  PIECES,
  parsePlacement,
  placementProblem,
  setSquare,
  squareName,
  withPlacement,
  type Piece,
  type Square,
} from '~/chess/fen';
import { color, font, radius, space } from '~/theme/tokens';

const GLYPH: Record<Piece, string> = {
  K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
  k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
};

interface Props {
  readonly fen: string;
  readonly lowConfidenceSquares?: readonly number[];
  readonly onApply: (fen: string) => void;
  readonly onCancel: () => void;
}

/**
 * Tap-to-correct editor for a misread position.
 *
 * Editing works the way a physical board does: pick up a piece, put it down. Tapping
 * a square with no piece selected clears it, which makes removing a phantom piece --
 * the most common misread -- a single tap.
 */
export function CorrectionScreen({ fen, lowConfidenceSquares, onApply, onCancel }: Props) {
  const { width } = useWindowDimensions();
  const [squares, setSquares] = useState<Square[]>(() => parsePlacement(fen));
  const [held, setHeld] = useState<Piece | null>(null);

  const problem = useMemo(() => placementProblem(squares), [squares]);
  const boardSize = Math.min(width - space.xl * 2, 400);
  const dirty = useMemo(() => withPlacement(fen, squares) !== fen, [fen, squares]);

  const onPressSquare = (index: number) => {
    setSquares((current) => setSquare(current, index, held));
  };

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <TouchableOpacity onPress={onCancel} accessibilityRole="button" hitSlop={12}>
          <Text style={styles.cancel}>Cancel</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Correct the position</Text>
        <TouchableOpacity
          onPress={() => onApply(withPlacement(fen, squares))}
          disabled={!!problem || !dirty}
          accessibilityRole="button"
          hitSlop={12}
        >
          <Text style={[styles.apply, (!!problem || !dirty) && styles.applyDisabled]}>Apply</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.hint}>
        {held
          ? `Tap a square to place the ${describe(held)}.`
          : 'Pick a piece below, or tap a square to clear it.'}
      </Text>

      <View style={styles.boardWrapper}>
        <BoardDiagram
          squares={squares}
          size={boardSize}
          lowConfidence={lowConfidenceSquares}
          onPressSquare={onPressSquare}
        />
      </View>

      {lowConfidenceSquares?.length ? (
        <Text style={styles.uncertainNote}>
          Outlined squares are the ones the camera was least sure about
          {` (${lowConfidenceSquares.map(squareName).join(', ')}).`}
        </Text>
      ) : null}

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.palette}>
        <PaletteButton label="Clear" active={held === null} onPress={() => setHeld(null)} />
        {PIECES.map((piece) => (
          <PaletteButton
            key={piece}
            label={GLYPH[piece]}
            active={held === piece}
            onPress={() => setHeld(piece)}
          />
        ))}
      </ScrollView>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
    </View>
  );
}

function PaletteButton({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      onPress={onPress}
      accessibilityRole="button"
      style={[styles.paletteItem, active && styles.paletteItemActive]}
    >
      <Text style={styles.paletteLabel}>{label}</Text>
    </TouchableOpacity>
  );
}

function describe(piece: Piece): string {
  const names: Record<string, string> = {
    k: 'king', q: 'queen', r: 'rook', b: 'bishop', n: 'knight', p: 'pawn',
  };
  const colour = piece === piece.toUpperCase() ? 'white' : 'black';
  return `${colour} ${names[piece.toLowerCase()]}`;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.background, padding: space.xl, gap: space.md },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { ...font.body, color: color.text, fontWeight: '600' },
  cancel: { ...font.body, color: color.textMuted },
  apply: { ...font.body, color: color.accent, fontWeight: '700' },
  applyDisabled: { color: color.textFaint },
  hint: { ...font.caption, color: color.textMuted },
  boardWrapper: { alignItems: 'center', marginVertical: space.sm },
  uncertainNote: { ...font.caption, color: color.warn, textAlign: 'center' },
  palette: { flexGrow: 0 },
  paletteItem: {
    minWidth: 52,
    height: 52,
    marginRight: space.sm,
    borderRadius: radius.md,
    backgroundColor: color.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: space.sm,
  },
  paletteItemActive: { borderColor: color.accent, borderWidth: 2 },
  paletteLabel: { ...font.body, fontSize: 26, color: color.text },
  problem: { ...font.caption, color: color.bad, textAlign: 'center' },
});
