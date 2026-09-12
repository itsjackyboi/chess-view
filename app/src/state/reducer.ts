/**
 * Session state reduction.
 *
 * Pure, and separate from the zustand binding in store.ts, because the rule it
 * enforces is the most important one in the product and deserves direct tests:
 *
 *   **Analysis is shown as current only when it provably is.**
 *
 * Every path that could leave a stale, mismatched or low-confidence evaluation on
 * screen has to fail closed. A frozen evaluation presented as live is worse than no
 * evaluation, because the user cannot tell the difference.
 */

import type { Eval, Position, ServerMessage, SessionStatus } from '@chessview/protocol';

import type { ConnectionState } from '~/net/client';

/** Below this, the server is telling us it is unsure; we do not present analysis. */
export const CONFIDENCE_THRESHOLD = 0.6;

export interface MoveRecord {
  readonly ply: number;
  readonly san: string;
  readonly fen: string;
}

export interface SessionState {
  readonly connection: ConnectionState;
  readonly connectionDetail?: string;
  readonly status: SessionStatus;
  readonly statusDetail?: string;
  readonly sessionId: string | null;
  readonly engineName: string | null;
  readonly fen: string | null;
  readonly confidence: number;
  readonly ply: number;
  /** Squares the detector was least sure about, for the correction editor. */
  readonly lowConfidenceSquares: readonly number[];
  readonly history: readonly MoveRecord[];
  readonly evaluation: Eval | null;
  readonly paused: boolean;
  readonly error: string | null;
}

export const INITIAL_STATE: SessionState = {
  connection: 'idle',
  status: 'calibrating',
  sessionId: null,
  engineName: null,
  fen: null,
  confidence: 0,
  ply: 0,
  lowConfidenceSquares: [],
  history: [],
  evaluation: null,
  paused: false,
  error: null,
};

/** Why analysis is not being presented as live, or null when it is. */
export type StaleReason =
  | 'disconnected'
  | 'reconnecting'
  | 'paused'
  | 'no-board'
  | 'unclear'
  | 'low-light'
  | 'low-confidence'
  | 'position-changed'
  | 'waiting';

export interface AnalysisView {
  readonly isLive: boolean;
  readonly reason: StaleReason | null;
  /** Present whenever we have one, even when stale -- the UI dims it rather than
   *  hiding it, so the layout does not jump. `isLive` governs presentation. */
  readonly evaluation: Eval | null;
}

export function deriveAnalysis(state: SessionState): AnalysisView {
  const stale = (reason: StaleReason): AnalysisView => ({
    isLive: false,
    reason,
    evaluation: state.evaluation,
  });

  // Connection first: nothing on screen is current if we are not connected, no
  // matter how good the last evaluation looked.
  if (state.connection === 'failed' || state.connection === 'idle') return stale('disconnected');
  if (state.connection !== 'connected') return stale('reconnecting');
  if (state.paused) return stale('paused');

  switch (state.status) {
    case 'no_board':
      return stale('no-board');
    case 'unclear':
      return stale('unclear');
    case 'low_light':
      return stale('low-light');
    case 'paused':
      return stale('paused');
    default:
      break;
  }

  if (state.confidence < CONFIDENCE_THRESHOLD) return stale('low-confidence');
  if (!state.evaluation || !state.fen) return stale('waiting');

  // The decisive check: an evaluation for a different position is not this
  // position's evaluation, however recently it arrived.
  if (state.evaluation.fen !== state.fen) return stale('position-changed');

  return { isLive: true, reason: null, evaluation: state.evaluation };
}

export function applyServerMessage(state: SessionState, message: ServerMessage): SessionState {
  switch (message.type) {
    case 'sessionReady':
      return {
        ...state,
        sessionId: message.sessionId,
        engineName: message.engine.name,
        error: null,
        // A fresh session starts from nothing; a resumed one keeps what we had, and
        // the server re-sends the authoritative position immediately after.
        ...(message.resumed
          ? {}
          : { history: [], evaluation: null, ply: 0, fen: null, lowConfidenceSquares: [] }),
      };

    case 'position':
      return applyPosition(state, message);

    case 'eval':
      // Late updates for a position we have moved past are dropped here as well as
      // server-side: either end may race, and the cost of showing one is a wrong
      // evaluation flashing over the right one.
      if (state.fen && message.fen !== state.fen) return state;
      return { ...state, evaluation: message };

    case 'state':
      return {
        ...state,
        status: message.status,
        statusDetail: message.detail ?? undefined,
      };

    case 'error':
      return { ...state, error: message.message };

    case 'pong':
      return state;

    default:
      return state;
  }
}

function applyPosition(state: SessionState, message: Position): SessionState {
  const history = message.moveSan
    ? [...state.history, { ply: message.ply, san: message.moveSan, fen: message.fen }]
    : state.history;

  return {
    ...state,
    fen: message.fen,
    confidence: message.confidence,
    ply: message.ply,
    lowConfidenceSquares: message.lowConfidenceSquares ?? [],
    history,
    // The previous evaluation describes the previous position. Clearing it here is
    // what stops the overlay showing the old assessment under the new board while
    // the engine works.
    evaluation: message.fen === state.fen ? state.evaluation : null,
  };
}

export function applyConnectionState(
  state: SessionState,
  connection: ConnectionState,
  detail?: string,
): SessionState {
  return { ...state, connection, connectionDetail: detail };
}
