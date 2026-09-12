/* eslint-disable */
/**
 * GENERATED FILE -- do not edit.
 * Source of truth: packages/protocol/chessview_protocol/messages.py
 * Regenerate with: npm run protocol:generate
 */

export type ClientMessage = Hello | Calibrate | OverrideFen | Pause | Ping;
export type Type = 'hello';
export type Protocolversion = number;
export type Platform = 'ios' | 'android' | 'test';
export type Appversion = string;
export type Resumesessionid = string | null;
export type Type1 = 'calibrate';
/**
 * @minItems 4
 * @maxItems 4
 */
export type Corners = [Corner, Corner, Corner, Corner];
export type X = number;
export type Y = number;
export type Orientation = 'white_bottom' | 'black_bottom';
export type Startfen = string | null;
export type Type2 = 'overrideFen';
export type Fen = string;
export type Type3 = 'pause';
export type Paused = boolean;
export type Type4 = 'ping';
export type Ts = number;
export type ServerMessage = SessionReady | Position | Eval | State | Error | Pong;
export type Type5 = 'sessionReady';
export type Sessionid = string;
export type Protocolversion1 = number;
export type Name = string;
export type Version = string;
export type Multipv = number;
export type Resumed = boolean;
export type Type6 = 'position';
export type Fen1 = string;
export type Confidence = number;
export type PositionSource = 'initial' | 'detector' | 'manual';
export type Ply = number;
export type Moveuci = string | null;
export type Movesan = string | null;
export type Lowconfidencesquares = number[];
export type Ts1 = number;
export type Type7 = 'eval';
export type Fen2 = string;
export type Depth = number;
export type Final = boolean;
export type Multipv1 = number;
export type Scorecp = number | null;
export type Scoremate = number | null;
export type Pv = string[];
export type San = string[];
export type Lines = EvalLine[];
export type Ts2 = number;
export type Type8 = 'state';
/**
 * What the pipeline is currently able to tell the user.
 *
 * The client renders each of these distinctly. ``UNCLEAR`` exists so we can say
 * "I don't know" instead of showing a confident evaluation for a position we are
 * not sure about -- showing stale or guessed analysis is worse than showing none.
 */
export type SessionStatus = 'calibrating' | 'tracking' | 'unclear' | 'no_board' | 'low_light' | 'paused';
export type Detail = string | null;
export type Type9 = 'error';
export type ErrorCode =
  | 'protocol_version_mismatch'
  | 'bad_message'
  | 'frame_too_large'
  | 'not_calibrated'
  | 'illegal_position'
  | 'engine_unavailable'
  | 'session_not_found'
  | 'rate_limited'
  | 'internal';
export type Message = string;
export type Fatal = boolean;
export type Type10 = 'pong';
export type Ts3 = number;
export type Serverts = number;
export type Seq = number;
export type Capturets = number;
export type Motion = boolean;

/**
 * Generated from chessview_protocol.messages (v1). Do not edit by hand.
 */
export interface ChessViewProtocol {
  ClientMessage: ClientMessage;
  ServerMessage: ServerMessage;
  FrameHeader: FrameHeader;
}
export interface Hello {
  type?: Type;
  protocolVersion: Protocolversion;
  client: ClientInfo;
  resumeSessionId?: Resumesessionid;
}
export interface ClientInfo {
  platform: Platform;
  appVersion: Appversion;
}
/**
 * Lock the board geometry and seed the starting position.
 *
 * ``corners`` are the four board corners in the client's *rectified* frame of
 * reference, clockwise from the corner nearest the user. The client owns the
 * homography (it runs at camera frame rate on-device); the server records the
 * corners only for diagnostics and for redrawing the guide on resume.
 */
export interface Calibrate {
  type?: Type1;
  corners: Corners;
  orientation: Orientation;
  startFen?: Startfen;
}
export interface Corner {
  x: X;
  y: Y;
}
/**
 * A manual correction from the 2D board editor.
 *
 * Respected as authoritative until the detector observes a further change, so a
 * user who fixes a misread piece does not have it immediately overwritten.
 */
export interface OverrideFen {
  type?: Type2;
  fen: Fen;
}
export interface Pause {
  type?: Type3;
  paused: Paused;
}
export interface Ping {
  type?: Type4;
  ts: Ts;
}
export interface SessionReady {
  type?: Type5;
  sessionId: Sessionid;
  protocolVersion: Protocolversion1;
  engine: EngineInfo;
  resumed?: Resumed;
}
export interface EngineInfo {
  name: Name;
  version: Version;
  multipv: Multipv;
}
/**
 * A committed position. Only ever sent when the position actually changed.
 *
 * The server debounces and applies a stability check before committing, so the
 * client can treat every ``Position`` as a real move rather than a candidate.
 */
export interface Position {
  type?: Type6;
  fen: Fen1;
  confidence: Confidence;
  source: PositionSource;
  ply: Ply;
  moveUci?: Moveuci;
  moveSan?: Movesan;
  lowConfidenceSquares?: Lowconfidencesquares;
  ts: Ts1;
}
/**
 * A progressive evaluation update.
 *
 * Several of these arrive per position, at increasing depth. ``final`` marks the
 * last one. Carrying ``fen`` lets the client discard updates that belong to a
 * position it has already moved past.
 */
export interface Eval {
  type?: Type7;
  fen: Fen2;
  depth: Depth;
  final: Final;
  lines: Lines;
  ts: Ts2;
}
/**
 * One MultiPV line.
 *
 * Exactly one of ``score_cp``/``score_mate`` is set. Both are normalised to
 * **white's point of view** before leaving the server: Stockfish reports from the
 * side to move, which would make the evaluation bar flip on every move.
 */
export interface EvalLine {
  multipv: Multipv1;
  scoreCp?: Scorecp;
  scoreMate?: Scoremate;
  pv: Pv;
  san?: San;
}
export interface State {
  type?: Type8;
  status: SessionStatus;
  detail?: Detail;
}
export interface Error {
  type?: Type9;
  code: ErrorCode;
  message: Message;
  fatal?: Fatal;
}
export interface Pong {
  type?: Type10;
  ts: Ts3;
  serverTs: Serverts;
}
/**
 * Metadata packed into the binary frame alongside the JPEG payload.
 */
export interface FrameHeader {
  seq: Seq;
  captureTs: Capturets;
  motion?: Motion;
}
