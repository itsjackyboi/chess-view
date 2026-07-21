import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import WebSocket from 'ws';
import { createRelay } from '../src/relay.js';
import { signUuid } from '../src/tokens.js';

const PLUGIN_SECRET = 'plugin-secret';
const TOKEN_SECRET = 'token-secret';
const UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

// Silence the relay's operational logging during tests.
const quietLogger = { info() {}, warn() {}, error() {} };

// Start a relay on an ephemeral port; returns helpers and a teardown.
async function startRelay(t, { ackTimeoutMs = 200 } = {}) {
  const relay = createRelay({
    pluginSecret: PLUGIN_SECRET,
    tokenSecret: TOKEN_SECRET,
    clientDir: '/nonexistent', // static serving unused in these tests
    logger: quietLogger,
    ackTimeoutMs,
  });
  await new Promise((resolve) => relay.server.listen(0, resolve));
  const port = relay.server.address().port;

  const sockets = [];
  const track = (ws) => { sockets.push(ws); return ws; };

  // Single teardown: hard-terminate every client socket and the ws server's
  // server-side sockets, then close the HTTP server. Without terminating the
  // live WebSockets, server.close() never fires its callback and the hook hangs.
  t.after(async () => {
    for (const ws of sockets) { try { ws.terminate(); } catch { /* ignore */ } }
    for (const ws of relay.wss.clients) { try { ws.terminate(); } catch { /* ignore */ } }
    await new Promise((resolve) => relay.wss.close(() => resolve()));
    await new Promise((resolve) => relay.server.close(() => resolve()));
  });

  return {
    port,
    state: relay.state,
    async connectPlugin(secret = PLUGIN_SECRET) {
      const ws = track(new WebSocket(`ws://localhost:${port}/plugin`, {
        headers: { 'x-plugin-secret': secret },
      }));
      return ws;
    },
    async connectClient(uuid, token = signUuid(uuid, TOKEN_SECRET)) {
      const ws = track(new WebSocket(`ws://localhost:${port}/client?token=${encodeURIComponent(token)}`));
      return ws;
    },
  };
}

// Resolve with the next parsed JSON message on a socket.
function nextMessage(ws) {
  return once(ws, 'message').then(([data]) => JSON.parse(data.toString()));
}

test('region_change is routed to the matching client as play_region', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');
  const client = await relay.connectClient(UUID);
  await once(client, 'open');

  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'tavern',
    url: 'https://soundcloud.com/x/sets/tavern', volume: 80,
  }));

  const msg = await nextMessage(client);
  assert.deepEqual(msg, {
    type: 'play_region', region: 'tavern',
    url: 'https://soundcloud.com/x/sets/tavern', volume: 80,
  });
});

test('missing volume defaults to 100', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');
  const client = await relay.connectClient(UUID);
  await once(client, 'open');

  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'harbor',
    url: 'https://soundcloud.com/x/sets/harbor',
  }));

  const msg = await nextMessage(client);
  assert.equal(msg.volume, 100);
});

test('ack cancels the retry so no resend arrives', async (t) => {
  const relay = await startRelay(t, { ackTimeoutMs: 100 });
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');
  const client = await relay.connectClient(UUID);
  await once(client, 'open');

  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'tavern',
    url: 'u', volume: 50,
  }));
  await nextMessage(client); // first delivery
  client.send(JSON.stringify({ type: 'ack', player: UUID, region: 'tavern' }));

  // Wait well past the ack timeout; there must be no second message.
  let resent = false;
  client.on('message', () => { resent = true; });
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(resent, false);
  assert.equal(relay.state.pendingAcks.has(UUID), false);
});

test('no ack triggers resends up to the attempt cap', async (t) => {
  const relay = await startRelay(t, { ackTimeoutMs: 80 });
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');
  const client = await relay.connectClient(UUID);
  await once(client, 'open');

  let deliveries = 0;
  client.on('message', () => { deliveries += 1; });

  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'tavern', url: 'u', volume: 50,
  }));

  // 1 initial + up to 2 resends = 3 total (MAX_ACK_ATTEMPTS).
  await new Promise((r) => setTimeout(r, 500));
  assert.equal(deliveries, 3);
  assert.equal(relay.state.pendingAcks.has(UUID), false);
});

test('reconnecting client immediately gets the last command replayed', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');

  // Player was in the tavern before their browser (re)connects.
  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'tavern', url: 'u', volume: 70,
  }));
  // Give the relay a tick to store lastCommand.
  await new Promise((r) => setTimeout(r, 50));

  const client = await relay.connectClient(UUID);
  await once(client, 'open');
  const msg = await nextMessage(client);
  assert.equal(msg.type, 'play_region');
  assert.equal(msg.region, 'tavern');
  assert.equal(msg.volume, 70);
});

test('player_quit drops lastCommand and tells the client to stop', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');
  const client = await relay.connectClient(UUID);
  await once(client, 'open');

  plugin.send(JSON.stringify({
    type: 'region_change', player: UUID, region: 'tavern', url: 'u', volume: 70,
  }));
  await nextMessage(client);
  client.send(JSON.stringify({ type: 'ack', player: UUID, region: 'tavern' }));

  plugin.send(JSON.stringify({ type: 'player_quit', player: UUID }));
  const msg = await nextMessage(client);
  assert.equal(msg.type, 'stop');
  assert.equal(relay.state.lastCommand.has(UUID), false);
});

test('bad client token is rejected at upgrade', async (t) => {
  const relay = await startRelay(t);
  const client = await relay.connectClient(UUID, 'totally-invalid-token');
  const [err] = await once(client, 'error');
  assert.match(String(err.message), /401|Unexpected server response/);
});

test('plugin without the shared secret is rejected', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin('wrong-secret');
  const [err] = await once(plugin, 'error');
  assert.match(String(err.message), /401|Unexpected server response/);
});

test('plugin receives client_status when a client connects and disconnects', async (t) => {
  const relay = await startRelay(t);
  const plugin = await relay.connectPlugin();
  await once(plugin, 'open');

  const client = await relay.connectClient(UUID);
  await once(client, 'open');
  const up = await nextMessage(plugin);
  assert.deepEqual(up, { type: 'client_status', player: UUID, connected: true });

  client.close();
  const down = await nextMessage(plugin);
  assert.deepEqual(down, { type: 'client_status', player: UUID, connected: false });
});

test('ping from client gets a pong', async (t) => {
  const relay = await startRelay(t);
  const client = await relay.connectClient(UUID);
  await once(client, 'open');
  client.send(JSON.stringify({ type: 'ping' }));
  const msg = await nextMessage(client);
  assert.equal(msg.type, 'pong');
});
