import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { pairMoves } from '~/chess/moveList';
import type { MoveRecord } from '~/state/reducer';
import { color, font, radius, space } from '~/theme/tokens';

interface Props {
  readonly history: readonly MoveRecord[];
  readonly engineName: string | null;
  readonly paused: boolean;
  readonly onTogglePause: () => void;
  readonly onCorrect: () => void;
  readonly onEndSession: () => void;
  readonly onClose: () => void;
}

/**
 * Session controls and move history.
 *
 * Kept off the live view deliberately: the camera screen shows only what the user
 * needs mid-game, and everything else lives one tap away so the board stays visible.
 */
export function SessionSheet({
  history,
  engineName,
  paused,
  onTogglePause,
  onCorrect,
  onEndSession,
  onClose,
}: Props) {
  return (
    <View style={styles.sheet}>
      <View style={styles.header}>
        <Text style={styles.title}>Session</Text>
        <TouchableOpacity onPress={onClose} accessibilityRole="button" hitSlop={12}>
          <Text style={styles.close}>Done</Text>
        </TouchableOpacity>
      </View>

      {engineName ? <Text style={styles.engine}>{engineName}</Text> : null}

      <Text style={styles.label}>MOVES</Text>
      <ScrollView style={styles.history} contentContainerStyle={styles.historyContent}>
        {history.length === 0 ? (
          <Text style={styles.empty}>No moves yet.</Text>
        ) : (
          pairMoves(history).map(({ number, white, black }) => (
            <View key={number} style={styles.moveRow}>
              <Text style={styles.moveNumber}>{number}.</Text>
              <Text style={styles.move}>{white ?? ''}</Text>
              <Text style={styles.move}>{black ?? ''}</Text>
            </View>
          ))
        )}
      </ScrollView>

      <View style={styles.actions}>
        <TouchableOpacity style={styles.action} onPress={onTogglePause} accessibilityRole="button">
          <Text style={styles.actionLabel}>{paused ? 'Resume analysis' : 'Pause analysis'}</Text>
          <Text style={styles.actionHint}>
            {paused ? 'Restarts the camera stream' : 'Stops using data and battery'}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.action} onPress={onCorrect} accessibilityRole="button">
          <Text style={styles.actionLabel}>Correct the position</Text>
          <Text style={styles.actionHint}>Fix a misread piece on a 2D board</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.action, styles.destructive]}
          onPress={onEndSession}
          accessibilityRole="button"
        >
          <Text style={[styles.actionLabel, styles.destructiveLabel]}>End session</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    flex: 1,
    backgroundColor: color.background,
    padding: space.xl,
    gap: space.md,
  },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { ...font.title, color: color.text },
  close: { ...font.body, color: color.accent, fontWeight: '600' },
  engine: { ...font.caption, color: color.textFaint },
  label: { ...font.label, color: color.textFaint, marginTop: space.sm },
  history: { flex: 1 },
  historyContent: { gap: space.xs, paddingBottom: space.lg },
  empty: { ...font.body, color: color.textFaint },
  moveRow: { flexDirection: 'row', gap: space.md, alignItems: 'baseline' },
  moveNumber: { ...font.mono, fontSize: 13, color: color.textFaint, minWidth: 32 },
  move: { ...font.mono, fontSize: 14, color: color.text, minWidth: 72 },
  actions: { gap: space.sm },
  action: {
    padding: space.lg,
    borderRadius: radius.md,
    backgroundColor: color.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
  },
  actionLabel: { ...font.body, color: color.text, fontWeight: '600' },
  actionHint: { ...font.caption, color: color.textFaint, marginTop: 2 },
  destructive: { borderColor: 'rgba(248, 113, 113, 0.35)' },
  destructiveLabel: { color: color.bad },
});
