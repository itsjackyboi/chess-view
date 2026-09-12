import { useCallback, useMemo, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View, useWindowDimensions } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
} from 'react-native-reanimated';

import { cornersAreUsable, orderCorners, type Point } from '~/camera/homography';
import { color, font, radius, space } from '~/theme/tokens';

const HANDLE = 34;

interface Props {
  /** Receives the four corners in image coordinates, ordered TL, TR, BR, BL. */
  readonly onConfirm: (corners: Point[]) => void;
}

/**
 * Board alignment: drag four handles onto the board's corners.
 *
 * The corners the user places are exact, where automatic detection lands about
 * 0.6 squares off -- enough to cut pieces in half in the crops the classifier sees.
 * So this is the primary path and detection is only a starting guess.
 *
 * It is also where camera angle gets coached. Occlusion is geometric: at a shallow
 * angle a piece on the seventh rank hides one on the eighth, and no amount of
 * downstream cleverness recovers what the camera never saw.
 */
export function CalibrationOverlay({ onConfirm }: Props) {
  const { width, height } = useWindowDimensions();

  // A sensible default square in the middle of the frame, so a user pointing at a
  // reasonably framed board has very little dragging to do.
  const initial = useMemo<Point[]>(() => {
    const inset = width * 0.12;
    const top = height * 0.26;
    const side = width - inset * 2;
    return [
      { x: inset, y: top },
      { x: inset + side, y: top },
      { x: inset + side, y: top + side },
      { x: inset, y: top + side },
    ];
  }, [width, height]);

  const [corners, setCorners] = useState<Point[]>(initial);

  const usable = cornersAreUsable(corners, { width, height });

  const moveCorner = useCallback((index: number, point: Point) => {
    setCorners((current) => current.map((corner, i) => (i === index ? point : corner)));
  }, []);

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
      <Outline corners={corners} />

      {corners.map((corner, index) => (
        <CornerHandle
          key={index}
          corner={corner}
          bounds={{ width, height }}
          onMove={(point) => moveCorner(index, point)}
        />
      ))}

      <View style={styles.instructions}>
        <Text style={styles.title}>Line up the board</Text>
        <Text style={styles.body}>
          Drag each marker onto a corner of the board. Hold the phone above the board
          rather than level with it — a high angle stops tall pieces hiding the row
          behind them.
        </Text>

        {!usable.ok ? <Text style={styles.warning}>{usable.reason}</Text> : null}

        <TouchableOpacity
          style={[styles.button, !usable.ok && styles.buttonDisabled]}
          disabled={!usable.ok}
          onPress={() => onConfirm(orderCorners(corners))}
          accessibilityRole="button"
        >
          <Text style={[styles.buttonLabel, !usable.ok && styles.buttonLabelDisabled]}>
            Start analysing
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

/** The quad joining the four handles, drawn as four rotated segments.
 *  Cheaper than pulling in a drawing library for what is ultimately four lines. */
function Outline({ corners }: { corners: readonly Point[] }) {
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="none">
      {corners.map((from, index) => {
        const to = corners[(index + 1) % corners.length]!;
        const length = Math.hypot(to.x - from.x, to.y - from.y);
        const angle = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
        return (
          <View
            key={index}
            style={[
              styles.edge,
              {
                left: from.x,
                top: from.y,
                width: length,
                transform: [{ rotateZ: `${angle}deg` }],
              },
            ]}
          />
        );
      })}
    </View>
  );
}

function CornerHandle({
  corner,
  bounds,
  onMove,
}: {
  corner: Point;
  bounds: { width: number; height: number };
  onMove: (point: Point) => void;
}) {
  const x = useSharedValue(corner.x);
  const y = useSharedValue(corner.y);
  const startX = useSharedValue(0);
  const startY = useSharedValue(0);

  const pan = Gesture.Pan()
    .onBegin(() => {
      startX.value = x.value;
      startY.value = y.value;
    })
    .onUpdate((event) => {
      // Clamped to the frame: a handle dragged off-screen cannot be recovered.
      x.value = Math.min(bounds.width, Math.max(0, startX.value + event.translationX));
      y.value = Math.min(bounds.height, Math.max(0, startY.value + event.translationY));
    })
    .onEnd(() => {
      // Committed to React state only on release. Updating on every frame of the
      // drag would re-render the whole overlay at gesture rate.
      runOnJS(onMove)({ x: x.value, y: y.value });
    });

  const style = useAnimatedStyle(() => ({
    transform: [{ translateX: x.value - HANDLE / 2 }, { translateY: y.value - HANDLE / 2 }],
  }));

  return (
    <GestureDetector gesture={pan}>
      <Animated.View style={[styles.handle, style]}>
        <View style={styles.handleDot} />
      </Animated.View>
    </GestureDetector>
  );
}

const styles = StyleSheet.create({
  edge: {
    position: 'absolute',
    height: 2,
    backgroundColor: color.accent,
    transformOrigin: 'left center',
  },
  handle: {
    position: 'absolute',
    width: HANDLE,
    height: HANDLE,
    borderRadius: radius.pill,
    borderWidth: 2,
    borderColor: color.accent,
    backgroundColor: 'rgba(91, 157, 255, 0.22)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  handleDot: {
    width: 6,
    height: 6,
    borderRadius: radius.pill,
    backgroundColor: color.accent,
  },
  instructions: {
    position: 'absolute',
    left: space.lg,
    right: space.lg,
    bottom: space.xl,
    padding: space.xl,
    borderRadius: radius.lg,
    backgroundColor: color.overlay,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
    gap: space.md,
  },
  title: { ...font.title, color: color.text },
  body: { ...font.body, color: color.textMuted, lineHeight: 21 },
  warning: { ...font.caption, color: color.warn },
  button: {
    backgroundColor: color.accent,
    borderRadius: radius.md,
    paddingVertical: space.md,
    alignItems: 'center',
  },
  buttonDisabled: { backgroundColor: color.surface },
  buttonLabel: { ...font.body, color: '#08111F', fontWeight: '700' },
  buttonLabelDisabled: { color: color.textFaint },
});
