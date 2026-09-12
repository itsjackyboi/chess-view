import { describe, expect, it } from 'vitest';

import type { Eval, Position, ServerMessage } from '@chessview/protocol';

import {
  CONFIDENCE_THRESHOLD,
  INITIAL_STATE,
  type SessionState,
  applyConnectionState,
  applyServerMessage,
  deriveAnalysis,
} from './reducer';

const FEN_A = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const FEN_B = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';

const evaluation = (fen: string, depth = 18): Eval => ({
  type: 'eval',
  fen,
  depth,
  final: false,
  lines: [{ multipv: 1, scoreCp: 25, pv: ['e2e4'], san: ['e4'] }],
  ts: 0,
});

const position = (fen: string, overrides: Partial<Position> = {}): ServerMessage =>
  ({
    type: 'position',
    fen,
    confidence: 0.95,
    source: 'detector',
    ply: 1,
    ts: 0,
    ...overrides,
  }) as ServerMessage;

/** A state where analysis is legitimately live, as a baseline to perturb. */
function live(): SessionState {
  let state = applyConnectionState(INITIAL_STATE, 'connected');
  state = applyServerMessage(state, { type: 'state', status: 'tracking' } as ServerMessage);
  state = applyServerMessage(state, position(FEN_A, { moveSan: 'e4' }));
  state = applyServerMessage(state, evaluation(FEN_A) as ServerMessage);
  return state;
}

describe('deriveAnalysis', () => {
  it('is live when connected, tracking, confident and matching', () => {
    const view = deriveAnalysis(live());
    expect(view.isLive).toBe(true);
    expect(view.reason).toBeNull();
  });

  it.each([
    ['idle', 'disconnected'],
    ['failed', 'disconnected'],
    ['connecting', 'reconnecting'],
    ['reconnecting', 'reconnecting'],
  ] as const)('is not live while the connection is %s', (connection, reason) => {
    // The key reliability rule: a dropped connection must never leave a frozen
    // evaluation looking current.
    const view = deriveAnalysis(applyConnectionState(live(), connection));
    expect(view.isLive).toBe(false);
    expect(view.reason).toBe(reason);
  });

  it.each([
    ['no_board', 'no-board'],
    ['unclear', 'unclear'],
    ['low_light', 'low-light'],
  ] as const)('is not live when the server reports %s', (status, reason) => {
    const state = applyServerMessage(live(), { type: 'state', status } as ServerMessage);
    expect(deriveAnalysis(state)).toMatchObject({ isLive: false, reason });
  });

  it('is not live below the confidence threshold', () => {
    const state = applyServerMessage(
      live(),
      position(FEN_A, { confidence: CONFIDENCE_THRESHOLD - 0.01 }),
    );
    expect(deriveAnalysis(state)).toMatchObject({ isLive: false, reason: 'low-confidence' });
  });

  it('is not live when the evaluation belongs to another position', () => {
    // The decisive case: recency is not currency.
    const state: SessionState = { ...live(), evaluation: evaluation(FEN_B) };
    expect(deriveAnalysis(state)).toMatchObject({ isLive: false, reason: 'position-changed' });
  });

  it('is not live while paused', () => {
    expect(deriveAnalysis({ ...live(), paused: true })).toMatchObject({
      isLive: false,
      reason: 'paused',
    });
  });

  it('reports waiting before the first evaluation arrives', () => {
    const state: SessionState = { ...live(), evaluation: null };
    expect(deriveAnalysis(state)).toMatchObject({ isLive: false, reason: 'waiting' });
  });

  it('still exposes the stale evaluation so the layout does not jump', () => {
    const view = deriveAnalysis(applyConnectionState(live(), 'reconnecting'));
    expect(view.evaluation).not.toBeNull();
    expect(view.isLive).toBe(false);
  });
});

describe('applyServerMessage', () => {
  it('clears the evaluation when the position changes', () => {
    // Otherwise the old assessment sits under the new board while the engine works.
    const state = applyServerMessage(live(), position(FEN_B, { ply: 2 }));
    expect(state.evaluation).toBeNull();
    expect(state.fen).toBe(FEN_B);
  });

  it('keeps the evaluation when the same position is re-sent', () => {
    const state = applyServerMessage(live(), position(FEN_A));
    expect(state.evaluation).not.toBeNull();
  });

  it('drops an evaluation for a position already moved past', () => {
    const state = applyServerMessage(live(), evaluation(FEN_B) as ServerMessage);
    expect(state.evaluation?.fen).toBe(FEN_A);
  });

  it('records moves in history but not the initial position', () => {
    let state = applyConnectionState(INITIAL_STATE, 'connected');
    state = applyServerMessage(state, position(FEN_A, { source: 'initial', ply: 0 }));
    expect(state.history).toHaveLength(0);

    state = applyServerMessage(state, position(FEN_B, { moveSan: 'e4', ply: 1 }));
    expect(state.history).toEqual([{ ply: 1, san: 'e4', fen: FEN_B }]);
  });

  it('resets game state for a fresh session', () => {
    const state = applyServerMessage(live(), {
      type: 'sessionReady',
      sessionId: 'new',
      protocolVersion: 1,
      engine: { name: 'Stockfish', version: '', multipv: 3 },
      resumed: false,
    } as ServerMessage);

    expect(state.history).toEqual([]);
    expect(state.evaluation).toBeNull();
    expect(state.fen).toBeNull();
  });

  it('preserves game state when resuming', () => {
    const state = applyServerMessage(live(), {
      type: 'sessionReady',
      sessionId: 'same',
      protocolVersion: 1,
      engine: { name: 'Stockfish', version: '', multipv: 3 },
      resumed: true,
    } as ServerMessage);

    expect(state.history).toHaveLength(1);
    expect(state.fen).toBe(FEN_A);
  });

  it('records a status detail so the UI can explain itself', () => {
    const state = applyServerMessage(live(), {
      type: 'state',
      status: 'unclear',
      detail: 'no legal move explains the observed board',
    } as ServerMessage);

    expect(state.statusDetail).toBe('no legal move explains the observed board');
  });

  it('surfaces errors without discarding the session', () => {
    const state = applyServerMessage(live(), {
      type: 'error',
      code: 'bad_message',
      message: 'nope',
      fatal: false,
    } as ServerMessage);

    expect(state.error).toBe('nope');
    expect(state.fen).toBe(FEN_A);
  });
});
