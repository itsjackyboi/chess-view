/**
 * The app's single store: the reducer in reducer.ts bound to a live connection.
 *
 * The client holds a render-only mirror of state the server owns. Nothing here
 * predicts or extrapolates -- on reconnect the server re-sends the truth.
 */

import { create } from 'zustand';

import type { ServerMessage } from '@chessview/protocol';

import { ChessViewClient, type ConnectionState, type SocketLike } from '~/net/client';
import {
  INITIAL_STATE,
  type AnalysisView,
  type SessionState,
  applyConnectionState,
  applyServerMessage,
  deriveAnalysis,
} from '~/state/reducer';

interface SessionStore extends SessionState {
  readonly client: ChessViewClient | null;
  analysis(): AnalysisView;
  start(url: string, appVersion: string, platform: 'ios' | 'android'): void;
  stop(): void;
  calibrate(corners: { x: number; y: number }[], startFen?: string): void;
  correctPosition(fen: string): void;
  setPaused(paused: boolean): void;
  sendFrame(seq: number, jpeg: Uint8Array, motion: boolean): boolean;
}

export const useSession = create<SessionStore>((set, get) => ({
  ...INITIAL_STATE,
  client: null,

  analysis: () => deriveAnalysis(get()),

  start: (url, appVersion, platform) => {
    get().client?.close();

    const client = new ChessViewClient({
      url,
      appVersion,
      platform,
      createSocket: (target) => new WebSocket(target) as unknown as SocketLike,
      callbacks: {
        onState: (connection: ConnectionState, detail?: string) =>
          set((state) => applyConnectionState(state, connection, detail)),
        onMessage: (message: ServerMessage) =>
          set((state) => applyServerMessage(state, message)),
      },
    });

    set({ ...INITIAL_STATE, client });
    client.connect();
  },

  stop: () => {
    get().client?.close();
    set({ ...INITIAL_STATE, client: null });
  },

  calibrate: (corners, startFen) => {
    get().client?.send({
      type: 'calibrate',
      corners: corners as never,
      orientation: 'white_bottom',
      ...(startFen ? { startFen } : {}),
    } as never);
  },

  correctPosition: (fen) => {
    get().client?.send({ type: 'overrideFen', fen } as never);
    // Optimism is unwarranted here: the server validates the FEN and answers with
    // a position message, so the UI waits for that rather than assuming success.
  },

  setPaused: (paused) => {
    get().client?.send({ type: 'pause', paused } as never);
    set({ paused });
  },

  sendFrame: (seq, jpeg, motion) => {
    const client = get().client;
    if (!client) return false;
    return client.sendFrame({ seq, captureTs: Date.now(), motion }, jpeg);
  },
}));
