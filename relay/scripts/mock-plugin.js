import 'dotenv/config';
import WebSocket from 'ws';

// Mock Minecraft plugin: connects to /plugin and sends a region_change so you
// can watch a real browser client crossfade without a running Paper server.
//
// Usage: node scripts/mock-plugin.js <uuid> <region> <soundcloud-url> [volume]

const uuid = process.argv[2] || '00000000-0000-0000-0000-000000000001';
const region = process.argv[3] || 'tavern';
const url = process.argv[4] || 'https://soundcloud.com/soundcloud/sets/soundcloud-hidden-gems';
const volume = Number(process.argv[5]) || 80;

const port = process.env.PORT || 8080;
const secret = process.env.PLUGIN_SECRET;

const ws = new WebSocket(`ws://localhost:${port}/plugin`, {
  headers: { 'x-plugin-secret': secret },
});

ws.on('open', () => {
  console.log('[mock-plugin] connected, sending region_change');
  ws.send(JSON.stringify({ type: 'region_change', player: uuid, region, url, volume }));
});
ws.on('message', (data) => console.log('[mock-plugin] <-', data.toString()));
ws.on('close', (code) => console.log('[mock-plugin] closed', code));
ws.on('error', (err) => console.error('[mock-plugin] error', err.message));
