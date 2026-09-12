/**
 * Public surface of the ChessView protocol package.
 *
 * The message *types* are generated from the pydantic models (see generated.ts);
 * this file adds the runtime pieces TypeScript needs and the generator cannot
 * express -- the binary frame codec and discriminator narrowing.
 */

export * from './generated';

import type {
  ClientMessage,
  Eval,
  Error as ProtocolError,
  FrameHeader,
  Pong,
  Position,
  ServerMessage,
  SessionReady,
  State,
} from './generated';

export const PROTOCOL_VERSION = 1;

/**
 * Frames above this are rejected by the server rather than buffered. Kept in sync
 * with MAX_FRAME_BYTES in chessview_protocol/messages.py by protocol.test.ts.
 */
export const MAX_FRAME_BYTES = 256 * 1024;

/* -------------------------------------------------------------------------- */
/* Binary frame codec                                                          */
/* -------------------------------------------------------------------------- */

export class FrameDecodeError extends Error {}

/**
 * Pack a camera frame as `[4-byte BE header length][JSON header][JPEG bytes]`.
 *
 * Header and payload travel in one WebSocket message so they stay atomic; as two
 * messages they could interleave with a concurrent send under load.
 */
export function encodeFrame(header: FrameHeader, jpeg: Uint8Array): Uint8Array {
  const headerBytes = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + headerBytes.length + jpeg.length);
  new DataView(out.buffer).setUint32(0, headerBytes.length, false);
  out.set(headerBytes, 4);
  out.set(jpeg, 4 + headerBytes.length);
  return out;
}

/** Inverse of {@link encodeFrame}. Used by tests and the desktop debug client. */
export function decodeFrame(payload: Uint8Array): { header: FrameHeader; jpeg: Uint8Array } {
  if (payload.length < 4) {
    throw new FrameDecodeError('frame is too short to contain a header length');
  }
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  const headerLen = view.getUint32(0, false);
  const end = 4 + headerLen;
  if (end > payload.length) {
    throw new FrameDecodeError('header length exceeds frame size');
  }
  const header = JSON.parse(new TextDecoder().decode(payload.subarray(4, end))) as FrameHeader;
  return { header, jpeg: payload.subarray(end) };
}

/* -------------------------------------------------------------------------- */
/* Narrowing                                                                   */
/* -------------------------------------------------------------------------- */

type ServerMessageMap = {
  sessionReady: SessionReady;
  position: Position;
  eval: Eval;
  state: State;
  error: ProtocolError;
  pong: Pong;
};

/**
 * Narrow a decoded server message by its `type` discriminator.
 *
 * Prefer this over comparing `msg.type` inline: adding a message variant in
 * messages.py then fails to typecheck at every call site that must handle it.
 */
export function isServerMessage<K extends keyof ServerMessageMap>(
  msg: ServerMessage,
  type: K,
): msg is ServerMessageMap[K] {
  return msg.type === type;
}

export function parseServerMessage(raw: string): ServerMessage {
  return JSON.parse(raw) as ServerMessage;
}

export function serializeClientMessage(msg: ClientMessage): string {
  return JSON.stringify(msg);
}
