import { useCallback, useEffect, useState } from 'react';
import { AppState, Modal, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  Camera,
  useCameraDevice,
  useCameraPermission,
} from 'react-native-vision-camera';

import type { Point } from '~/camera/homography';
import { useFrameStreamer } from '~/camera/useFrameStreamer';
import { AnalysisPanel } from '~/components/AnalysisPanel';
import { EvaluationBar } from '~/components/EvaluationBar';
import { StatusBanner } from '~/components/StatusBanner';
import { APP_VERSION, PLATFORM, SERVICE_URL } from '~/config';
import { SessionSheet } from '~/screens/SessionSheet';
import { CalibrationOverlay } from '~/screens/CalibrationOverlay';
import { CorrectionScreen } from '~/screens/CorrectionScreen';
import { STARTING_FEN } from '~/chess/fen';
import { useSession } from '~/state/store';
import { color, font, radius, space } from '~/theme/tokens';

export function LiveScreen() {
  const insets = useSafeAreaInsets();
  const device = useCameraDevice('back');
  const { hasPermission, requestPermission } = useCameraPermission();

  const store = useSession();
  const analysis = useSession((state) => state.analysis());

  const [calibrated, setCalibrated] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [foreground, setForeground] = useState(true);

  // Streaming stops entirely in the background. Camera frames are the expensive
  // part of this app, and an app that keeps draining battery once put down is the
  // thing users remember about it.
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (next) =>
      setForeground(next === 'active'),
    );
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (!hasPermission) void requestPermission();
  }, [hasPermission, requestPermission]);

  useEffect(() => {
    store.start(SERVICE_URL, APP_VERSION, PLATFORM);
    return () => store.stop();
    // Intentionally once: the connection outlives re-renders and manages its own
    // reconnection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Streaming also stops while the correction editor is open: the user is telling
  // us what is on the board, so continuing to overwrite it from the camera would
  // fight them.
  const streaming = calibrated && foreground && !store.paused && !correcting;
  const { frameProcessor } = useFrameStreamer(streaming);

  const onCalibrate = useCallback(
    (corners: Point[]) => {
      store.calibrate(corners);
      setCalibrated(true);
    },
    [store],
  );

  const onEndSession = useCallback(() => {
    store.stop();
    setCalibrated(false);
    setSheetOpen(false);
    store.start(SERVICE_URL, APP_VERSION, PLATFORM);
  }, [store]);

  if (!hasPermission) {
    return (
      <Message
        title="Camera access needed"
        body="ChessView reads the board through the camera. Nothing is recorded, and only the board itself is sent for analysis."
      />
    );
  }

  if (device == null) {
    return <Message title="No camera available" body="This device has no back camera to use." />;
  }

  const best = analysis.evaluation?.lines?.[0];

  return (
    <View style={styles.root}>
      <Camera
        style={StyleSheet.absoluteFill}
        device={device}
        // Suspended in the background: the preview and the frame processor both stop.
        isActive={foreground}
        frameProcessor={streaming ? frameProcessor : undefined}
      />

      <View style={[styles.top, { paddingTop: insets.top + space.sm }]} pointerEvents="box-none">
        <StatusBanner reason={analysis.reason} detail={store.statusDetail} />
      </View>

      {!calibrated ? (
        <CalibrationOverlay onConfirm={onCalibrate} />
      ) : (
        <>
          <View style={[styles.evalRail, { top: insets.top + 80 }]} pointerEvents="none">
            <EvaluationBar line={best} isLive={analysis.isLive} />
          </View>

          <View
            style={[styles.bottom, { paddingBottom: insets.bottom + space.md }]}
            pointerEvents="box-none"
          >
            <AnalysisPanel
              evaluation={analysis.evaluation}
              isLive={analysis.isLive}
              ply={store.ply}
            />
            <TouchableOpacity
              style={styles.sessionButton}
              onPress={() => setSheetOpen(true)}
              accessibilityRole="button"
            >
              <Text style={styles.sessionLabel}>
                Session · {store.history.length} {store.history.length === 1 ? 'move' : 'moves'}
              </Text>
            </TouchableOpacity>
          </View>
        </>
      )}

      <Modal visible={sheetOpen} animationType="slide" presentationStyle="pageSheet">
        <SessionSheet
          history={store.history}
          engineName={store.engineName}
          paused={store.paused}
          onTogglePause={() => store.setPaused(!store.paused)}
          onCorrect={() => {
            setSheetOpen(false);
            setCorrecting(true);
          }}
          onEndSession={onEndSession}
          onClose={() => setSheetOpen(false)}
        />
      </Modal>

      <Modal visible={correcting} animationType="slide" presentationStyle="pageSheet">
        <CorrectionScreen
          fen={store.fen ?? STARTING_FEN}
          lowConfidenceSquares={store.lowConfidenceSquares}
          onApply={(fen) => {
            store.correctPosition(fen);
            setCorrecting(false);
          }}
          onCancel={() => setCorrecting(false)}
        />
      </Modal>
    </View>
  );
}

function Message({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.message}>
      <Text style={styles.messageTitle}>{title}</Text>
      <Text style={styles.messageBody}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: color.background },
  top: { position: 'absolute', top: 0, left: 0, right: 0, paddingHorizontal: space.lg },
  // The rail hugs the left edge so the middle of the frame -- where the board is --
  // stays unobstructed.
  evalRail: { position: 'absolute', left: space.lg, bottom: 220 },
  bottom: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: space.lg,
    gap: space.sm,
  },
  sessionButton: {
    alignSelf: 'center',
    paddingVertical: space.sm,
    paddingHorizontal: space.lg,
    borderRadius: radius.pill,
    backgroundColor: color.overlay,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: color.overlayBorder,
  },
  sessionLabel: { ...font.caption, color: color.textMuted, fontWeight: '600' },
  message: {
    flex: 1,
    backgroundColor: color.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: space.xxl,
    gap: space.md,
  },
  messageTitle: { ...font.title, color: color.text, textAlign: 'center' },
  messageBody: { ...font.body, color: color.textMuted, textAlign: 'center', lineHeight: 21 },
});
