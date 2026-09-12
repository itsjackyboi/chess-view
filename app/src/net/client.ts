/**
 * The client's single connection to the vision service.
 *
 * Owns connection lifecycle, reconnection and session resumption. It deliberately
 * does not own *interpretation* -- it hands decoded messages to a listener and lets
 * the store decide what the user sees. That split is what lets this be tested
 * without React Native or a real socket.
 */

import {
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  type ClientMessage,
  type FrameHeader,
  type ServerMessage,
  encodeFrame,
  parseServerMessage,
  serializeClientMessage,
} from '@chessview/protocol';

import { Backoff, DEFAULT_BACKOFF, type BackoffOptions } from './backoff';

export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  /** The socket dropped and we are waiting out a backoff delay before retrying. */
  | 'reconnecting'
  /** Terminal: the server rejected us in a way retrying cannot fix. */
  | 'failed';

/** The subset of WebSocket this client uses, so tests can supply a double. */
export interface SocketLike {
  binaryType: string;
  send(data: string | ArrayBufferView | ArrayBuffer): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onclose: ((event: { code?: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
}

export interface ClientCallbacks {
  onState(state: ConnectionState, detail?: string): void;
  onMessage(message: ServerMessage): void;
}

export interface ClientOptions {
  url: string;
  appVersion: string;
  platform: 'ios' | 'android' | 'test';
  createSocket(url: string): SocketLike;
  callbacks: ClientCallbacks;
  backoff?: BackoffOptions;
  random?: () => number;
  setTimeoutFn?: (fn: () => void, ms: number) => number;
  clearTimeoutFn?: (handle: number) => void;
}

export class ChessViewClient {
  private socket: SocketLike | null = null;
  private state: ConnectionState = 'idle';
  private readonly backoff: Backoff;
  private retryHandle: number | null = null;
  /** Kept across reconnects so the server can restore the game in progress. */
  private sessionId: string | null = null;
  /** Distinguishes a deliberate close from a dropped connection. */
  private closedByUs = false;
  private framesDropped = 0;

  constructor(private readonly options: ClientOptions) {
    this.backoff = new Backoff(options.backoff ?? DEFAULT_BACKOFF, options.random);
  }

  get connectionState(): ConnectionState {
    return this.state;
  }

  get currentSessionId(): string | null {
    return this.sessionId;
  }

  /** Frames refused because the socket was not open. Surfaced for diagnostics. */
  get droppedFrames(): number {
    return this.framesDropped;
  }

  connect(): void {
    if (this.state === 'connecting' || this.state === 'connected') return;
    this.closedByUs = false;
    this.open();
  }

  private open(): void {
    this.setState('connecting');
    let socket: SocketLike;
    try {
      socket = this.options.createSocket(this.options.url);
    } catch (error) {
      this.scheduleRetry(String(error));
      return;
    }

    this.socket = socket;
    socket.binaryType = 'arraybuffer';

    socket.onopen = () => {
      // The handshake, not the socket opening, is what makes a session usable --
      // so `connected` is only reported once the server answers with sessionReady.
      this.send({
        type: 'hello',
        protocolVersion: PROTOCOL_VERSION,
        client: { platform: this.options.platform, appVersion: this.options.appVersion },
        ...(this.sessionId ? { resumeSessionId: this.sessionId } : {}),
      } as ClientMessage);
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') return;
      let message: ServerMessage;
      try {
        message = parseServerMessage(event.data);
      } catch {
        return; // A malformed server frame is not worth tearing down the session.
      }
      this.handle(message);
    };

    socket.onerror = () => {
      // Errors are always followed by a close; handling it there keeps one path.
    };

    socket.onclose = (event) => {
      this.socket = null;
      if (this.closedByUs) {
        this.setState('idle');
        return;
      }
      this.scheduleRetry(event?.reason || 'connection lost');
    };
  }

  private handle(message: ServerMessage): void {
    if (message.type === 'sessionReady') {
      this.sessionId = message.sessionId;
      this.backoff.reset();
      this.setState('connected');
    } else if (message.type === 'error' && message.fatal) {
      // A fatal error is a rejection, not an outage. Retrying an incompatible
      // protocol version or a refused session just loops.
      this.closedByUs = true;
      this.setState('failed', message.message);
      this.socket?.close();
      this.socket = null;
      return;
    }
    this.options.callbacks.onMessage(message);
  }

  private scheduleRetry(detail: string): void {
    if (this.state === 'failed') return;
    this.setState('reconnecting', detail);

    const delay = this.backoff.next();
    // React Native's setTimeout returns a number, unlike Node's Timeout object.
    const schedule =
      this.options.setTimeoutFn ??
      ((fn: () => void, ms: number) => setTimeout(fn, ms) as unknown as number);
    this.retryHandle = schedule(() => {
      this.retryHandle = null;
      if (!this.closedByUs) this.open();
    }, delay);
  }

  send(message: ClientMessage): boolean {
    if (!this.socket || this.state === 'failed') return false;
    try {
      this.socket.send(serializeClientMessage(message));
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Send one camera frame.
   *
   * Returns false rather than queueing when the socket is not open. Buffering
   * frames through an outage would spend the reconnect delivering images of
   * positions the board has already moved past.
   */
  sendFrame(header: FrameHeader, jpeg: Uint8Array): boolean {
    if (!this.socket || this.state !== 'connected') {
      this.framesDropped += 1;
      return false;
    }
    const payload = encodeFrame(header, jpeg);
    if (payload.length > MAX_FRAME_BYTES) {
      this.framesDropped += 1;
      return false;
    }
    try {
      this.socket.send(payload);
      return true;
    } catch {
      this.framesDropped += 1;
      return false;
    }
  }

  /** Close deliberately. Does not reconnect, and forgets the session. */
  close(): void {
    this.closedByUs = true;
    const clear =
      this.options.clearTimeoutFn ?? ((handle: number) => clearTimeout(handle));
    if (this.retryHandle !== null) {
      clear(this.retryHandle);
      this.retryHandle = null;
    }
    this.socket?.close();
    this.socket = null;
    this.sessionId = null;
    this.setState('idle');
  }

  private setState(state: ConnectionState, detail?: string): void {
    if (this.state === state) return;
    this.state = state;
    this.options.callbacks.onState(state, detail);
  }
}
