import 'dotenv/config';
import { signUuid } from '../src/tokens.js';

// Usage: node scripts/sign-token.js <uuid>
// Prints a signed client token (and a ready-to-open local URL) for testing.
const uuid = process.argv[2] || '00000000-0000-0000-0000-000000000001';
const secret = process.env.TOKEN_SECRET;
if (!secret) {
  console.error('TOKEN_SECRET must be set (see .env.example)');
  process.exit(1);
}

const token = signUuid(uuid, secret);
const port = process.env.PORT || 8080;
console.log('uuid: ', uuid);
console.log('token:', token);
console.log('url:  ', `http://localhost:${port}/?token=${encodeURIComponent(token)}`);
