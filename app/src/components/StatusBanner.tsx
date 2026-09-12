import { StyleSheet, Text, View } from 'react-native';

import type { StaleReason } from '~/state/reducer';
import { color, font, radius, space } from '~/theme/tokens';

interface Props {
  readonly reason: StaleReason | null;
  readonly detail?: string;
}

/**
 * Says, in plain words, why analysis is not currently being shown.
 *
 * The product rule is that we never silently freeze: if the evaluation on screen is
 * not current, the user is told which of the several possible reasons applies, so
 * they can act on it -- move their hand, find more light, or wait out a reconnect.
 */
export function StatusBanner({ reason, detail }: Props) {
  if (reason === null) return null;

  const { label, tone } = describe(reason);

  return (
    <View style={[styles.banner, { borderColor: tone }]}>
      <View style={[styles.dot, { backgroundColor: tone }]} />
      <View style={styles.copy}>
        <Text style={styles.label}>{label}</Text>
        {detail ? (
          <Text style={styles.detail} numberOfLines={2}>
            {detail}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

function describe(reason: StaleReason): { label: string; tone: string } {
  switch (reason) {
    case 'disconnected':
      return { label: 'Disconnected — analysis paused', tone: color.bad };
    case 'reconnecting':
      return { label: 'Reconnecting — analysis paused', tone: color.warn };
    case 'paused':
      return { label: 'Paused', tone: color.textMuted };
    case 'no-board':
      return { label: 'Point the camera at the board', tone: color.warn };
    case 'unclear':
      return { label: "Can't read the board", tone: color.warn };
    case 'low-light':
      return { label: 'Too dark to read the pieces', tone: color.warn };
    case 'low-confidence':
      return { label: 'Not sure about this position', tone: color.warn };
    case 'position-changed':
      return { label: 'Analysing…', tone: color.accent };
    case 'waiting':
      return { label: 'Analysing…', tone: color.accent };
  }
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
    paddingVertical: space.sm,
    paddingHorizontal: space.md,
    borderRadius: radius.md,
    borderWidth: 1,
    backgroundColor: color.overlay,
  },
  dot: { width: 8, height: 8, borderRadius: radius.pill },
  copy: { flex: 1 },
  label: { ...font.caption, color: color.text, fontWeight: '600' },
  detail: { ...font.caption, color: color.textMuted, fontSize: 12, marginTop: 2 },
});
