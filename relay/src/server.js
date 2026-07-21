import 'dotenv/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRelay } from './relay.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT) || 8080;
const PLUGIN_SECRET = process.env.PLUGIN_SECRET;
const TOKEN_SECRET = process.env.TOKEN_SECRET;

if (!PLUGIN_SECRET || !TOKEN_SECRET) {
  console.error('[relay] PLUGIN_SECRET and TOKEN_SECRET must be set (see .env.example)');
  process.exit(1);
}

const clientDir = process.env.CLIENT_DIR
  ? path.resolve(process.env.CLIENT_DIR)
  : path.resolve(__dirname, '..', '..', 'client');

const { server } = createRelay({
  pluginSecret: PLUGIN_SECRET,
  tokenSecret: TOKEN_SECRET,
  clientDir,
});

server.listen(PORT, () => {
  console.log(`[relay] listening on http://0.0.0.0:${PORT}`);
  console.log(`[relay] serving client from ${clientDir}`);
  console.log('[relay] plugin -> ws://<host>:' + PORT + '/plugin   client -> ws://<host>:' + PORT + '/client?token=<signed-uuid>');
});

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => {
    console.log(`[relay] ${sig} received, shutting down`);
    server.close(() => process.exit(0));
  });
}
