import crypto from 'node:crypto';

// Client tokens are `<uuid>.<base64url-hmac>` where the HMAC is
// HMAC-SHA256(uuid) keyed by TOKEN_SECRET. The plugin's TokenSigner.java
// produces byte-identical tokens so join links verify here. Keep the two
// implementations in lock-step.

function base64url(buf) {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Return the base64url HMAC-SHA256 of `uuid` keyed by `secret`. */
export function signature(uuid, secret) {
  return base64url(crypto.createHmac('sha256', secret).update(uuid, 'utf8').digest());
}

/** Build a signed token for a player UUID. */
export function signUuid(uuid, secret) {
  return `${uuid}.${signature(uuid, secret)}`;
}

/**
 * Verify a token and return the UUID it encodes, or null if invalid.
 * Uses a constant-time comparison to avoid signature timing leaks.
 */
export function verifyToken(token, secret) {
  if (typeof token !== 'string') return null;
  const dot = token.lastIndexOf('.');
  if (dot <= 0) return null;
  const uuid = token.slice(0, dot);
  const provided = token.slice(dot + 1);
  const expected = signature(uuid, secret);
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return null;
  if (!crypto.timingSafeEqual(a, b)) return null;
  return uuid;
}
