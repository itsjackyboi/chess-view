import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { isLightSquare, squareName, type Square } from '~/chess/fen';
import { color, font, radius } from '~/theme/tokens';

/** Unicode chess glyphs. Both colours use the *solid* glyphs and are separated by
 *  fill colour instead: the outline set renders almost invisibly on a light square. */
const GLYPH: Record<string, string> = {
  K: '♚', Q: '♛', R: '♜', B: '♝', N: '♞', P: '♟',
  k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
};

interface Props {
  readonly squares: readonly Square[];
  readonly size: number;
  readonly selected?: number | null;
  readonly lowConfidence?: readonly number[];
  readonly onPressSquare?: (index: number) => void;
}

/**
 * A 2D board, for correcting what the camera misread.
 *
 * Corrections happen here rather than on the live camera feed: tapping a moving,
 * perspective-skewed video frame to pick out one square is unusable, and the
 * position is what needs fixing, not the image.
 */
export function BoardDiagram({ squares, size, selected, lowConfidence, onPressSquare }: Props) {
  const squareSize = size / 8;
  const uncertain = new Set(lowConfidence ?? []);

  return (
    <View style={[styles.board, { width: size, height: size }]}>
      {squares.map((piece, index) => {
        const light = isLightSquare(index);
        return (
          <TouchableOpacity
            key={index}
            activeOpacity={onPressSquare ? 0.6 : 1}
            disabled={!onPressSquare}
            onPress={() => onPressSquare?.(index)}
            accessibilityRole="button"
            accessibilityLabel={`${squareName(index)}${piece ? `, ${piece}` : ', empty'}`}
            style={[
              styles.square,
              {
                width: squareSize,
                height: squareSize,
                backgroundColor: light ? color.evalWhite : color.evalBlack,
              },
              // Highlighting the squares the model was least sure about points the
              // user straight at what probably needs fixing.
              uncertain.has(index) && styles.uncertain,
              selected === index && styles.selected,
            ]}
          >
            {piece ? (
              <Text
                style={[
                  styles.piece,
                  { fontSize: squareSize * 0.72 },
                  piece === piece.toUpperCase() ? styles.whitePiece : styles.blackPiece,
                ]}
              >
                {GLYPH[piece]}
              </Text>
            ) : null}
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  board: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    borderRadius: radius.sm,
    overflow: 'hidden',
  },
  square: { alignItems: 'center', justifyContent: 'center' },
  piece: { ...font.body, lineHeight: undefined, includeFontPadding: false },
  // Outlined rather than filled, so the glyph reads against either square colour.
  whitePiece: { color: '#FFFFFF', textShadowColor: '#000', textShadowRadius: 2 },
  blackPiece: { color: '#101318', textShadowColor: '#CCC', textShadowRadius: 1 },
  uncertain: { borderWidth: 2, borderColor: color.warn },
  selected: { borderWidth: 3, borderColor: color.accent },
});
