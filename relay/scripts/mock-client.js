import 'dotenv/config';
import WebSocket from 'ws';
import { signUuid } from '../src/tokens.js';

// Mock browser client: connects to /client with a signed token, logs any
// play_region commands, and auto-acks them (like the real client does after a
// crossfade). Useful for exercising the relay without a browser.
//
// Usage: node scripts/mock-client.js <uuid> [--no-ack]

const uuid = process.argv[2] || '00000000-0000-0000-0000-000000000001';
const autoAck = !process.argv.includes('--no-ack');

const port = process.env.PORT || 8080;
const token = signUuid(uuid, process.env.TOKEN_SECRET);

const ws = new WebSocket(`ws://localhost:${port}/client?token=${encodeURIComponent(token)}`);

ws.on('open', () => {
  console.log('[mock-client] connected as', uuid);
  setInterval(() => ws.readyState === ws.OPEN && ws.send(JSON.stringify({ type: 'ping' })), 20000);
});
ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  console.log('[mock-client] <-', msg);
  if (msg.type === 'play_region' && autoAck) {
    ws.send(JSON.stringify({ type: 'ack', player: uuid, region: msg.region }));
    console.log('[mock-client] -> ack', msg.region);
  }
});
ws.on('close', (code) => console.log('[mock-client] closed', code));
ws.on('error', (err) => console.error('[mock-client] error', err.message));
