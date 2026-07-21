import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { WebSocketServer } from 'ws';
import { verifyToken } from './tokens.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const DEFAULT_ACK_TIMEOUT_MS = 3000;
const MAX_ACK_ATTEMPTS = 3;

/**
 * Build the relay: an HTTP server (static client + websocket upgrades) plus
 * all in-memory routing state. Returns { server, wss, state } so callers and
 * tests can start/stop it and inspect state.
 *
 * @param {object} opts
 * @param {string} opts.pluginSecret  shared secret required on /plugin
 * @param {string} opts.tokenSecret   HMAC key for verifying client tokens
 * @param {string} [opts.clientDir]   static dir to serve at GET /
 * @param {console} [opts.logger]     logger (defaults to console)
 */
export function createRelay({ pluginSecret, tokenSecret, clientDir, logger = console, ackTimeoutMs = DEFAULT_ACK_TIMEOUT_MS }) {
  const app = express();
  const staticDir = clientDir || path.resolve(__dirname, '..', '..', 'client');
  app.use(express.static(staticDir));
  app.get('/healthz', (_req, res) => res.json({ ok: true }));

  const server = http.createServer(app);

  // Routing state.
  const state = {
    pluginSocket: null,               // the single authenticated plugin socket
    clients: new Map(),               // uuid -> browser WebSocket
    lastCommand: new Map(),           // uuid -> last play_region command object
    pendingAcks: new Map(),           // uuid -> { attempts, timer, region }
  };

  const wss = new WebSocketServer({ noServer: true });

  function send(ws, obj) {
    if (ws && ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function notifyPluginStatus(uuid, connected) {
    send(state.pluginSocket, { type: 'client_status', player: uuid, connected });
  }

  function clearPendingAck(uuid) {
    const pending = state.pendingAcks.get(uuid);
    if (pending) {
      clearTimeout(pending.timer);
      state.pendingAcks.delete(uuid);
    }
  }

  // Forward a play_region command to a player's browser and arm ack/retry.
  function deliverCommand(uuid, command, attempt = 1) {
    const client = state.clients.get(uuid);
    if (!send(client, command)) return; // no live browser; replay handles it later

    clearPendingAck(uuid);
    const timer = setTimeout(() => {
      if (attempt >= MAX_ACK_ATTEMPTS) {
        logger.warn(`[relay] no ack from ${uuid} for region ${command.region} after ${attempt} attempts`);
        state.pendingAcks.delete(uuid);
        return;
      }
      logger.warn(`[relay] resend play_region to ${uuid} (attempt ${attempt + 1})`);
      deliverCommand(uuid, command, attempt + 1);
    }, ackTimeoutMs);
    // Prevent the retry timer from keeping the process (or tests) alive.
    if (typeof timer.unref === 'function') timer.unref();
    state.pendingAcks.set(uuid, { attempts: attempt, timer, region: command.region });
  }

  function handlePluginMessage(msg) {
    switch (msg.type) {
      case 'region_change': {
        const uuid = msg.player;
        if (!uuid) return;
        const command = {
          type: 'play_region',
          region: msg.region,
          url: msg.url,
          volume: typeof msg.volume === 'number' ? msg.volume : 100,
        };
        state.lastCommand.set(uuid, command);
        deliverCommand(uuid, command);
        break;
      }
      case 'player_quit': {
        const uuid = msg.player;
        if (!uuid) return;
        state.lastCommand.delete(uuid);
        clearPendingAck(uuid);
        send(state.clients.get(uuid), { type: 'stop' });
        break;
      }
      default:
        logger.warn(`[relay] unknown plugin message type: ${msg.type}`);
    }
  }

  function handleClientMessage(uuid, msg) {
    switch (msg.type) {
      case 'ack':
        clearPendingAck(uuid);
        break;
      case 'ping':
        send(state.clients.get(uuid), { type: 'pong' });
        break;
      default:
        logger.warn(`[relay] unknown client message type from ${uuid}: ${msg.type}`);
    }
  }

  function registerPlugin(ws) {
    // Only one plugin connection is expected; the newest wins.
    if (state.pluginSocket && state.pluginSocket !== ws) {
      try { state.pluginSocket.close(4000, 'replaced by new plugin connection'); } catch { /* ignore */ }
    }
    state.pluginSocket = ws;
    logger.info('[relay] plugin connected');

    ws.on('message', (data) => {
      let msg;
      try { msg = JSON.parse(data.toString()); } catch { return; }
      handlePluginMessage(msg);
    });
    ws.on('close', () => {
      if (state.pluginSocket === ws) state.pluginSocket = null;
      logger.info('[relay] plugin disconnected');
    });
    ws.on('error', () => { /* close handler cleans up */ });
  }

  function registerClient(ws, uuid) {
    // Replace any stale socket for this player.
    const prior = state.clients.get(uuid);
    if (prior && prior !== ws) {
      try { prior.close(4001, 'replaced by new client connection'); } catch { /* ignore */ }
    }
    state.clients.set(uuid, ws);
    logger.info(`[relay] client connected: ${uuid}`);
    notifyPluginStatus(uuid, true);

    // Reconnect replay: resume the player to their current region immediately.
    const last = state.lastCommand.get(uuid);
    if (last) deliverCommand(uuid, last);

    ws.on('message', (data) => {
      let msg;
      try { msg = JSON.parse(data.toString()); } catch { return; }
      handleClientMessage(uuid, msg);
    });
    ws.on('close', () => {
      if (state.clients.get(uuid) === ws) {
        state.clients.delete(uuid);
        clearPendingAck(uuid);
        notifyPluginStatus(uuid, false);
      }
      logger.info(`[relay] client disconnected: ${uuid}`);
    });
    ws.on('error', () => { /* close handler cleans up */ });
  }

  // Route websocket upgrades by path, authenticating before registering.
  server.on('upgrade', (req, socket, head) => {
    let url;
    try {
      url = new URL(req.url, 'http://localhost');
    } catch {
      socket.destroy();
      return;
    }

    if (url.pathname === '/plugin') {
      if (req.headers['x-plugin-secret'] !== pluginSecret) {
        socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
        socket.destroy();
        return;
      }
      wss.handleUpgrade(req, socket, head, (ws) => registerPlugin(ws));
      return;
    }

    if (url.pathname === '/client') {
      const uuid = verifyToken(url.searchParams.get('token'), tokenSecret);
      if (!uuid) {
        socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
        socket.destroy();
        return;
      }
      wss.handleUpgrade(req, socket, head, (ws) => registerClient(ws, uuid));
      return;
    }

    socket.destroy();
  });

  return { app, server, wss, state };
}
