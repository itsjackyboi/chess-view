import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { color, font, radius, space } from '~/theme/tokens';

interface Props {
  readonly onConfirm: () => void;
}

/**
 * The one-time board alignment step.
 *
 * Also where the camera angle gets coached. Piece occlusion is geometric -- at a low
 * angle a piece on the seventh rank hides one on the eighth -- so nudging the user
 * to shoot from above is worth more to detection accuracy than anything downstream
 * can recover. See docs/architecture.md.
 */
export function CalibrationOverlay({ onConfirm }: Props) {
  return (
    <View style={styles.container} pointerEvents="box-none">
      <View style={styles.guide} pointerEvents="none">
        <Corner style={styles.topLeft} />
        <Corner style={styles.topRight} />
        <Corner style={styles.bottomLeft} />
        <Corner style={styles.bottomRight} />
      </View>

      <View style={styles.instructions}>
        <Text style={styles.title}>Line up the board</Text>
        <Text style={styles.body}>
          Fit the whole board inside the guide, and hold the phone above it rather
          than level with it — a high angle stops tall pieces hiding the row behind.
        </Text>
        <TouchableOpacity style={styles.button} onPress={onConfirm} accessibilityRole="button">
          <Text style={styles.buttonLabel}>Start analysing</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

function Corner({ style }: { style: object }) {
  return <View style={[styles.corner, style]} />;
}

const THICKNESS = 3;
const LENGTH = 36;

const styles = StyleSheet.create({
  container: { ...StyleSheet.absoluteFillObject, justifyContent: 'space-between' },
  guide: {
    position: 'absolute',
    top: '18%',
    left: '8%',
    right: '8%',
    aspectRatio: 1,
  },
  corner: {
    position: 'absolute',
    width: LENGTH,
    height: LENGTH,
    borderColor: color.accent,
  },
  topLeft: { top: 0, left: 0, borderTopWidth: THICKNESS, borderLeftWidth: THICKNESS },
  topRight: { top: 0, right: 0, borderTopWidth: THICKNESS, borderRightWidth: THICKNESS },
  bottomLeft: { bottom: 0, left: 0, borderBottomWidth: THICKNESS, borderLeftWidth: THICKNESS },
  bottomRight: { bottom: 0, right: 0, borderBottomWidth: THICKNESS, borderRightWidth: THICKNESS },
  instructions: {
    marginTop: 'auto',
    margin: space.lg,
    padding: space.xl,
    borderRadius: radius.lg,
    backgroundColor: color.overlay,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
    gap: space.md,
  },
  title: { ...font.title, color: color.text },
  body: { ...font.body, color: color.textMuted, lineHeight: 21 },
  button: {
    backgroundColor: color.accent,
    borderRadius: radius.md,
    paddingVertical: space.md,
    alignItems: 'center',
    marginTop: space.xs,
  },
  buttonLabel: { ...font.body, color: '#08111F', fontWeight: '700' },
});
