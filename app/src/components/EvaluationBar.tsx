import { useEffect } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import type { EvalLine } from '@chessview/protocol';

import { formatScore, scoreToBarFraction } from '~/chess/format';
import { EVAL_BAR_DURATION_MS, color, font, radius, space } from '~/theme/tokens';

interface Props {
  readonly line: EvalLine | undefined;
  readonly isLive: boolean;
}

/**
 * The evaluation bar: white's share of the position, bottom-up.
 *
 * Animated rather than snapped. Progressive analysis delivers several updates a
 * second as depth increases, and a bar that jumped on each one would read as
 * broken; easing between values turns that into a sense of the engine settling.
 */
export function EvaluationBar({ line, isLive }: Props) {
  const fraction = useSharedValue(0.5);

  useEffect(() => {
    fraction.value = withTiming(scoreToBarFraction(line), {
      duration: EVAL_BAR_DURATION_MS,
    });
  }, [line, fraction]);

  const whiteShare = useAnimatedStyle(() => ({
    height: `${fraction.value * 100}%`,
  }));

  return (
    <View style={styles.container}>
      <View style={[styles.track, !isLive && styles.stale]}>
        <Animated.View style={[styles.white, whiteShare]} />
      </View>
      <Text style={[styles.score, !isLive && styles.scoreStale]} numberOfLines={1}>
        {isLive ? formatScore(line) : '--'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', gap: space.sm },
  track: {
    width: 10,
    flex: 1,
    minHeight: 120,
    borderRadius: radius.pill,
    backgroundColor: color.evalBlack,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
    overflow: 'hidden',
    // The bar fills upward, so white's share grows from the bottom.
    justifyContent: 'flex-end',
  },
  white: { width: '100%', backgroundColor: color.evalWhite },
  stale: { opacity: 0.35 },
  score: {
    ...font.mono,
    ...font.caption,
    color: color.text,
    fontWeight: '600',
  },
  scoreStale: { color: color.textFaint },
});
