/**
 * Streams camera frames to the vision service.
 *
 * Runs the motion gate inside the frame processor worklet, on the camera's own
 * thread, so frames that are not worth sending never cross into JavaScript at all.
 * That is the point of doing it here rather than in the store: the cheapest frame is
 * one that is never marshalled.
 *
 * ## What is M0 and what is not
 *
 * Today this downscales the full camera frame and sends that. Two pieces land at M3
 * (see docs/roadmap.md), and both matter:
 *
 *   1. **Homography warp** -- sending only the rectified board crop, not the whole
 *      frame. That is the ~10x bandwidth saving *and* the privacy property: the room
 *      and anyone in it are cropped out before transmission, not discarded later.
 *   2. **JPEG encoding** -- needs a native frame-processor plugin. Until then the
 *      raw downscaled buffer goes out, which is larger than the ~22 KB the
 *      architecture budgets for.
 *
 * Until both exist, treat the bandwidth figures in docs/architecture.md as targets
 * rather than as measurements of this code.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useFrameProcessor } from 'react-native-vision-camera';
import { Worklets, useSharedValue } from 'react-native-worklets-core';
import { useResizePlugin } from 'vision-camera-resize-plugin';

import { DEFAULT_GATE, FrameGate, motionScore } from '~/camera/frameGate';
import { useSession } from '~/state/store';

/** The rectified board is square; 320px gives ~40px per square, ample for
 *  classifying a piece and small enough to keep the uplink cheap. */
export const FRAME_SIZE = 320;

export function useFrameStreamer(enabled: boolean) {
  const { resize } = useResizePlugin();
  const sendFrame = useSession((state) => state.sendFrame);

  const gate = useRef(new FrameGate(DEFAULT_GATE));
  const sequence = useRef(0);
  // Held on the worklet side so the comparison never crosses threads.
  const previousFrame = useSharedValue<Uint8Array | null>(null);

  useEffect(() => {
    if (!enabled) gate.current.reset();
  }, [enabled]);

  const deliver = useCallback(
    (buffer: Uint8Array, motion: boolean) => {
      sequence.current += 1;
      sendFrame(sequence.current, buffer, motion);
    },
    [sendFrame],
  );

  const deliverOnJs = Worklets.createRunOnJS(deliver);

  const frameProcessor = useFrameProcessor(
    (frame) => {
      'worklet';
      if (!enabled) return;

      const rgb = resize(frame, {
        scale: { width: FRAME_SIZE, height: FRAME_SIZE },
        pixelFormat: 'rgb',
        dataType: 'uint8',
      });

      const score = motionScore(previousFrame.value, rgb);
      previousFrame.value = rgb;

      const decision = gate.current.decide(Date.now(), score);
      if (!decision.send) return;

      deliverOnJs(rgb, decision.motion);
    },
    [enabled, resize, deliverOnJs, previousFrame],
  );

  return { frameProcessor, stats: () => gate.current.stats };
}
