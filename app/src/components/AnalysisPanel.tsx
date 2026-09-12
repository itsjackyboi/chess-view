import { StyleSheet, Text, View } from 'react-native';

import type { Eval } from '@chessview/protocol';

import { formatLine, formatScore } from '~/chess/format';
import { color, font, radius, space } from '~/theme/tokens';

interface Props {
  readonly evaluation: Eval | null;
  readonly isLive: boolean;
  readonly ply: number;
}

/**
 * Best move and candidate lines.
 *
 * Dimmed rather than hidden when analysis is not current: removing it would make
 * the overlay jump every time a hand crosses the board, and the greyed-out state
 * plus the status banner already say it is not live.
 */
export function AnalysisPanel({ evaluation, isLive, ply }: Props) {
  const lines = evaluation?.lines ?? [];
  const best = lines[0];

  return (
    <View style={[styles.panel, !isLive && styles.stale]}>
      <View style={styles.header}>
        <Text style={styles.label}>BEST MOVE</Text>
        {evaluation ? (
          <Text style={styles.depth}>
            depth {evaluation.depth}
            {evaluation.final ? '' : '…'}
          </Text>
        ) : null}
      </View>

      <View style={styles.bestRow}>
        <Text style={styles.bestMove} numberOfLines={1}>
          {best?.san?.[0] ?? '—'}
        </Text>
        <Text style={styles.bestScore}>{isLive ? formatScore(best) : '--'}</Text>
      </View>

      {lines.length > 1 ? (
        <View style={styles.alternatives}>
          {lines.slice(1).map((line) => (
            <View key={line.multipv} style={styles.alternativeRow}>
              <Text style={styles.alternativeScore}>{formatScore(line)}</Text>
              <Text style={styles.alternativeLine} numberOfLines={1}>
                {formatLine(line.san ?? [], ply, 4)}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      {best?.san?.length ? (
        <Text style={styles.principalVariation} numberOfLines={2}>
          {formatLine(best.san, ply)}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    backgroundColor: color.overlay,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
    padding: space.lg,
    gap: space.sm,
  },
  stale: { opacity: 0.45 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  label: { ...font.label, color: color.textFaint },
  depth: { ...font.mono, fontSize: 11, color: color.textFaint },
  bestRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  bestMove: { ...font.display, color: color.text },
  bestScore: { ...font.mono, fontSize: 20, fontWeight: '600', color: color.accent },
  alternatives: { gap: space.xs, marginTop: space.xs },
  alternativeRow: { flexDirection: 'row', gap: space.sm, alignItems: 'baseline' },
  alternativeScore: {
    ...font.mono,
    fontSize: 12,
    color: color.textMuted,
    minWidth: 52,
  },
  alternativeLine: { ...font.caption, color: color.textMuted, flex: 1 },
  principalVariation: {
    ...font.mono,
    fontSize: 12,
    color: color.textFaint,
    marginTop: space.xs,
  },
});
