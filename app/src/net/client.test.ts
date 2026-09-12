import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PROTOCOL_VERSION, decodeFrame, type ServerMessage } from '@chessview/protocol';

import { ChessViewClient, type ConnectionState, type SocketLike } from './client';

/** A controllable stand-in for a WebSocket. */
class FakeSocket implements SocketLike {
  binaryType = '';
  readonly sent: (string | ArrayBufferView | ArrayBuffer)[] = [];
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  send(data: string | ArrayBufferView | ArrayBuffer): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }

  /* --- test drivers --- */
  open(): void {
    this.onopen?.({});
  }

  deliver(message: ServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop(reason = 'connection lost'): void {
    this.onclose?.({ reason });
  }

  get textMessages(): Record<string, unknown>[] {
    return this.sent
      .filter((d): d is string => typeof d === 'string')
      .map((d) => JSON.parse(d) as Record<string, unknown>);
  }

  get binaryMessages(): Uint8Array[] {
    return this.sent.filter((d): d is Uint8Array => d instanceof Uint8Array);
  }
}

function ready(sessionId = 'sess-1', resumed = false): ServerMessage {
  return {
    type: 'sessionReady',
    sessionId,
    protocolVersion: PROTOCOL_VERSION,
    engine: { name: 'Stockfish', version: '', multipv: 3 },
    resumed,
  } as ServerMessage;
}

interface Harness {
  client: ChessViewClient;
  sockets: FakeSocket[];
  states: { state: ConnectionState; detail?: string }[];
  messages: ServerMessage[];
  runPendingTimers(): void;
}

function harness(): Harness {
  const sockets: FakeSocket[] = [];
  const states: { state: ConnectionState; detail?: string }[] = [];
  const messages: ServerMessage[] = [];
  let pending: (() => void)[] = [];

  const client = new ChessViewClient({
    url: 'wss://example.test/v1/session',
    appVersion: '0.1.0',
    platform: 'test',
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    callbacks: {
      onState: (state, detail) => states.push({ state, detail }),
      onMessage: (message) => messages.push(message),
    },
    random: () => 1,
    setTimeoutFn: (fn) => {
      pending.push(fn);
      return pending.length;
    },
    clearTimeoutFn: () => {
      pending = [];
    },
  });

  return {
    client,
    sockets,
    states,
    messages,
    runPendingTimers: () => {
      const due = pending;
      pending = [];
      due.forEach((fn) => fn());
    },
  };
}

describe('ChessViewClient', () => {
  let h: Harness;

  beforeEach(() => {
    h = harness();
  });

  it('sends the handshake on open', () => {
    h.client.connect();
    h.sockets[0]!.open();

    expect(h.sockets[0]!.textMessages[0]).toMatchObject({
      type: 'hello',
      protocolVersion: PROTOCOL_VERSION,
      client: { platform: 'test' },
    });
  });

  it('reports connected only once the server answers, not when the socket opens', () => {
    // An open socket whose handshake is never answered is not a usable session.
    h.client.connect();
    h.sockets[0]!.open();
    expect(h.client.connectionState).toBe('connecting');

    h.sockets[0]!.deliver(ready());
    expect(h.client.connectionState).toBe('connected');
  });

  it('reconnects after a dropped connection and resumes the session', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready('sess-42'));

    h.sockets[0]!.drop();
    expect(h.client.connectionState).toBe('reconnecting');

    h.runPendingTimers();
    h.sockets[1]!.open();

    expect(h.sockets[1]!.textMessages[0]).toMatchObject({ resumeSessionId: 'sess-42' });
  });

  it('does not retry after a fatal error', () => {
    // A protocol mismatch is a rejection, not an outage; retrying just loops.
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver({
      type: 'error',
      code: 'protocol_version_mismatch',
      message: 'server speaks protocol 2',
      fatal: true,
    } as ServerMessage);

    expect(h.client.connectionState).toBe('failed');
    h.runPendingTimers();
    expect(h.sockets).toHaveLength(1);
  });

  it('keeps the session alive through a non-fatal error', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());
    h.sockets[0]!.deliver({
      type: 'error',
      code: 'bad_message',
      message: 'nope',
      fatal: false,
    } as ServerMessage);

    expect(h.client.connectionState).toBe('connected');
  });

  it('encodes frames as binary with their header', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());

    const jpeg = new Uint8Array([0xff, 0xd8, 1, 2, 3]);
    expect(h.client.sendFrame({ seq: 7, captureTs: 99, motion: true }, jpeg)).toBe(true);

    const { header, jpeg: decoded } = decodeFrame(h.sockets[0]!.binaryMessages[0]!);
    expect(header).toEqual({ seq: 7, captureTs: 99, motion: true });
    expect(Array.from(decoded)).toEqual([0xff, 0xd8, 1, 2, 3]);
  });

  it('drops frames while disconnected rather than queueing them', () => {
    // Buffering through an outage would spend the reconnect sending images of
    // positions the board has already moved past.
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());
    h.sockets[0]!.drop();

    const jpeg = new Uint8Array([0xff, 0xd8]);
    expect(h.client.sendFrame({ seq: 1, captureTs: 0, motion: true }, jpeg)).toBe(false);
    expect(h.client.droppedFrames).toBe(1);

    h.runPendingTimers();
    h.sockets[1]!.open();
    h.sockets[1]!.deliver(ready());
    expect(h.sockets[1]!.binaryMessages).toHaveLength(0);
  });

  it('refuses oversized frames without sending them', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());

    const huge = new Uint8Array(300 * 1024);
    expect(h.client.sendFrame({ seq: 1, captureTs: 0, motion: true }, huge)).toBe(false);
    expect(h.sockets[0]!.binaryMessages).toHaveLength(0);
  });

  it('stops reconnecting once closed deliberately', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());

    h.client.close();
    expect(h.client.connectionState).toBe('idle');
    expect(h.sockets[0]!.closed).toBe(true);

    h.sockets[0]!.drop();
    h.runPendingTimers();
    expect(h.sockets).toHaveLength(1);
  });

  it('forgets the session on a deliberate close', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready('sess-9'));
    expect(h.client.currentSessionId).toBe('sess-9');

    h.client.close();
    expect(h.client.currentSessionId).toBeNull();
  });

  it('ignores a malformed server message without dropping the session', () => {
    h.client.connect();
    h.sockets[0]!.open();
    h.sockets[0]!.deliver(ready());

    h.sockets[0]!.onmessage?.({ data: '{not json' });
    expect(h.client.connectionState).toBe('connected');
  });

  it('survives a socket that throws on construction', () => {
    const states: ConnectionState[] = [];
    const client = new ChessViewClient({
      url: 'wss://example.test',
      appVersion: '0.1.0',
      platform: 'test',
      createSocket: () => {
        throw new Error('no network');
      },
      callbacks: { onState: (s) => states.push(s), onMessage: vi.fn() },
      random: () => 1,
      setTimeoutFn: () => 1,
      clearTimeoutFn: () => {},
    });

    client.connect();
    expect(states).toContain('reconnecting');
  });
});
